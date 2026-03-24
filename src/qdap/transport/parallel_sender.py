"""
Parallel chunk streaming — büyük payload'ları N stream'e böler.

Algoritma:
  1. Payload → N eşit parça (chunk)
  2. Her parça ayrı asyncio task olarak gönderilir
  3. Alıcı chunk_idx ile sıraya koyar, tamamlanınca birleştirir
  4. Herhangi bir chunk kaybolursa sadece o yeniden iletilir

Stream sayısı QFT scheduler'dan gelir:
  - MICRO/SMALL: 1 stream (küçük payload, overhead var)
  - MEDIUM: 2 stream
  - LARGE: 4 stream
  - JUMBO: 8 stream

Chunk wire format (Python struct, Rust bridge stream_id desteği beklenirken):
  [MAGIC(4)][ver(1)][type(1)][priority(2)][stream_id(2)]
  [chunk_idx(2)][total_chunks(2)][seq(4)][payload_len(4)]
  [payload_hash(32)][payload(N)]
  = 54B sabit header + payload
"""

import asyncio
import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Chunk frame wire format
_MAGIC         = b"\x51\x44\x41\x50"   # "QDAP"
_VERSION       = 1
_FRAME_TYPE    = 0x01                   # DATA
_HEADER_FMT    = "<4sBBHHHHII"          # magic(4)+ver(1)+type(1)+pri(2)+sid(2)+cidx(2)+tot(2)+seq(4)+len(4)
_HEADER_SIZE   = struct.calcsize(_HEADER_FMT)   # 22 bytes
_HASH_SIZE     = 32                     # SHA3-256

STREAM_COUNTS: Dict[str, int] = {
    "MICRO":  1,
    "SMALL":  1,
    "MEDIUM": 2,
    "LARGE":  4,
    "JUMBO":  8,
}


def plan_parallel_chunks(
    payload_size: int,
    chunk_size:   int,
    n_streams:    int,
) -> List[Tuple[int, int, int, int]]:
    """
    Paralel stream için chunk dağıtımını hesapla.
    Returns: [(stream_num, chunk_idx, start, end), ...]

    Python fallback — Rust bridge derlenmeden de çalışır.
    """
    if chunk_size == 0 or n_streams == 0:
        return []
    n_chunks = (payload_size + chunk_size - 1) // chunk_size
    plan = []
    for idx in range(n_chunks):
        start      = idx * chunk_size
        end        = min(start + chunk_size, payload_size)
        stream_num = idx % n_streams
        plan.append((stream_num, idx, start, end))
    return plan


@dataclass
class ChunkInfo:
    stream_id:    int
    chunk_idx:    int
    total_chunks: int
    data:         bytes
    seq:          int
    priority:     int = 500


@dataclass
class AssemblyBuffer:
    """Alıcı tarafında chunk'ları birleştirir."""
    stream_id:    int
    total_chunks: int
    received:     Dict[int, bytes] = field(default_factory=dict)
    created_at:   float = field(default_factory=time.time)

    def add(self, chunk_idx: int, data: bytes) -> bool:
        self.received[chunk_idx] = data
        return self.is_complete()

    def is_complete(self) -> bool:
        return len(self.received) == self.total_chunks

    def assemble(self) -> bytes:
        assert self.is_complete(), (
            f"Cannot assemble: have {len(self.received)}/{self.total_chunks} chunks"
        )
        return b"".join(
            self.received[i] for i in range(self.total_chunks)
        )

    def missing_chunks(self) -> List[int]:
        return [
            i for i in range(self.total_chunks)
            if i not in self.received
        ]


def _build_chunk_frame(chunk: ChunkInfo) -> bytes:
    """
    QFrame-compatible chunk wire format.
    Header: magic(4)+ver(1)+type(1)+priority(2)+stream_id(2)+
            chunk_idx(2)+total_chunks(2)+seq(4)+payload_len(4) = 22B
    Body:   SHA3-256(32) + payload(N)
    Total:  54B + len(payload)
    """
    payload_hash = hashlib.sha3_256(chunk.data).digest()
    header = struct.pack(
        _HEADER_FMT,
        _MAGIC,
        _VERSION,
        _FRAME_TYPE,
        chunk.priority & 0xFFFF,
        chunk.stream_id & 0xFFFF,
        chunk.chunk_idx & 0xFFFF,
        chunk.total_chunks & 0xFFFF,
        chunk.seq & 0xFFFFFFFF,
        len(chunk.data),
    )
    return header + payload_hash + chunk.data


def parse_chunk_frame(data: bytes) -> Optional[ChunkInfo]:
    """
    Chunk frame'i parse et.
    Returns ChunkInfo veya None (kısa/hatalı veri).
    """
    min_size = _HEADER_SIZE + _HASH_SIZE
    if len(data) < min_size:
        return None

    magic, ver, ftype, priority, stream_id, chunk_idx, total_chunks, seq, payload_len = \
        struct.unpack_from(_HEADER_FMT, data)

    if magic != _MAGIC:
        return None

    payload_start = _HEADER_SIZE + _HASH_SIZE
    payload_end   = payload_start + payload_len
    if len(data) < payload_end:
        return None

    stored_hash  = data[_HEADER_SIZE:_HEADER_SIZE + _HASH_SIZE]
    payload_data = data[payload_start:payload_end]

    if hashlib.sha3_256(payload_data).digest() != stored_hash:
        return None

    return ChunkInfo(
        stream_id=stream_id,
        chunk_idx=chunk_idx,
        total_chunks=total_chunks,
        data=payload_data,
        seq=seq,
        priority=priority,
    )


class ParallelSender:
    """
    Büyük payload'ı paralel stream'lerle gönderir.

    Usage:
        sender = ParallelSender(writer, strategy="LARGE")
        await sender.send(payload, priority=500, deadline_ms=5000)
    """

    def __init__(
        self,
        writer,
        strategy:   str = "MEDIUM",
        chunk_size: int = 65536,
    ):
        self.writer     = writer
        self.strategy   = strategy
        self.chunk_size = chunk_size
        self.n_streams  = STREAM_COUNTS.get(strategy, 2)
        self._stream_counter = 0

    def _next_stream_id(self) -> int:
        self._stream_counter += 1
        return self._stream_counter & 0xFFFF

    def _split(self, payload: bytes) -> List[bytes]:
        """Payload'ı eşit chunk'lara böl."""
        return [
            payload[i:i + self.chunk_size]
            for i in range(0, len(payload), self.chunk_size)
        ]

    async def send(
        self,
        payload:     bytes,
        priority:    int   = 500,
        deadline_ms: float = 5000.0,
    ) -> Tuple[int, float]:
        """
        Payload'ı paralel stream'lerle gönder.

        Returns:
            (bytes_sent, elapsed_ms)
        """
        chunks = self._split(payload)
        n      = len(chunks)
        sid    = self._next_stream_id()
        t0     = time.perf_counter()

        # Chunk'ları stream'lere round-robin dağıt
        streams: List[List[ChunkInfo]] = [[] for _ in range(self.n_streams)]
        for idx, chunk_data in enumerate(chunks):
            stream_num = idx % self.n_streams
            streams[stream_num].append(ChunkInfo(
                stream_id=sid,
                chunk_idx=idx,
                total_chunks=n,
                data=chunk_data,
                seq=idx,
                priority=priority,
            ))

        async def send_stream(chunk_list: List[ChunkInfo]) -> None:
            for chunk in chunk_list:
                frame = _build_chunk_frame(chunk)
                self.writer.write(frame)
            await self.writer.drain()

        await asyncio.gather(*[
            send_stream(stream_chunks)
            for stream_chunks in streams
            if stream_chunks
        ])

        elapsed = (time.perf_counter() - t0) * 1000
        return len(payload), elapsed


class ParallelReceiver:
    """
    Paralel stream'lerden gelen chunk'ları birleştirir.
    """

    def __init__(self, timeout_ms: float = 5000.0):
        self._buffers: Dict[int, AssemblyBuffer] = {}
        self._timeout = timeout_ms / 1000.0

    def on_chunk(
        self,
        stream_id:    int,
        chunk_idx:    int,
        total_chunks: int,
        data:         bytes,
    ) -> Optional[bytes]:
        """
        Gelen chunk'ı kaydet.
        Tüm chunk'lar tamamlandıysa birleştirilmiş payload'ı döndür.
        Henüz tamamlanmadıysa None döner.
        """
        if stream_id not in self._buffers:
            self._buffers[stream_id] = AssemblyBuffer(
                stream_id=stream_id,
                total_chunks=total_chunks,
            )

        buf = self._buffers[stream_id]

        # Timeout kontrolü
        if time.time() - buf.created_at > self._timeout:
            del self._buffers[stream_id]
            return None

        complete = buf.add(chunk_idx, data)
        if complete:
            payload = buf.assemble()
            del self._buffers[stream_id]
            return payload

        return None

    def missing_for_stream(self, stream_id: int) -> List[int]:
        buf = self._buffers.get(stream_id)
        return buf.missing_chunks() if buf else []
