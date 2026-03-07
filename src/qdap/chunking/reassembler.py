"""
Chunk Reassembler (Receiver Side) — Batch-Aware
===================================================

Supports both chunk frames (20B header) and batch frames (28B header).
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Awaitable

from qdap.chunking.chunk_qframe import (
    ChunkMetadata, CHUNK_HEADER_SIZE,
    BatchMetadata, BATCH_HEADER_SIZE,
)
from qdap.frame.qframe import SubframeType


@dataclass
class StreamBuffer:
    stream_id: bytes
    total_chunks: int
    chunks: Dict[int, bytes] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)

    @property
    def is_complete(self) -> bool:
        return len(self.chunks) == self.total_chunks

    @property
    def age_sec(self) -> float:
        return time.monotonic() - self.created_at

    def add_chunk(self, index: int, data: bytes):
        self.chunks[index] = data

    def reassemble(self) -> bytes:
        return b"".join(self.chunks[i] for i in range(self.total_chunks))


class ChunkReassembler:
    STREAM_TIMEOUT_SEC = 30.0

    def __init__(self):
        self._streams: Dict[bytes, StreamBuffer] = {}
        self._lock = asyncio.Lock()
        self.on_complete: Optional[Callable[[bytes, bytes], Awaitable[None]]] = None

    async def process_subframes(self, subframes: list) -> Optional[bytes]:
        """Process chunk or batch subframes. Auto-detects by header size."""
        meta_bytes = None
        data_bytes = None

        for sf in subframes:
            if sf.type == SubframeType.CTRL:
                meta_bytes = sf.payload
            elif sf.type == SubframeType.DATA:
                data_bytes = sf.payload

        if meta_bytes is None or data_bytes is None:
            return None

        # Detect batch (28B) vs chunk (20B) by header size
        if len(meta_bytes) >= BATCH_HEADER_SIZE:
            return await self._process_batch(meta_bytes, data_bytes)
        elif len(meta_bytes) >= CHUNK_HEADER_SIZE:
            return await self._process_chunk(meta_bytes, data_bytes)

        return None

    async def _process_batch(self, meta_bytes: bytes, data_bytes: bytes) -> Optional[bytes]:
        meta = BatchMetadata.from_bytes(meta_bytes)

        async with self._lock:
            if meta.stream_id not in self._streams:
                self._streams[meta.stream_id] = StreamBuffer(
                    stream_id=meta.stream_id, total_chunks=meta.total_batches,
                )
            buf = self._streams[meta.stream_id]
            buf.add_chunk(meta.batch_index, data_bytes)

            if buf.is_complete:
                payload = buf.reassemble()
                del self._streams[meta.stream_id]
                if self.on_complete:
                    await self.on_complete(meta.stream_id, payload)
                return payload
        return None

    async def _process_chunk(self, meta_bytes: bytes, data_bytes: bytes) -> Optional[bytes]:
        meta = ChunkMetadata.from_bytes(meta_bytes)

        async with self._lock:
            if meta.stream_id not in self._streams:
                self._streams[meta.stream_id] = StreamBuffer(
                    stream_id=meta.stream_id, total_chunks=meta.total_chunks,
                )
            buf = self._streams[meta.stream_id]
            buf.add_chunk(meta.chunk_index, data_bytes)

            if buf.is_complete:
                payload = buf.reassemble()
                del self._streams[meta.stream_id]
                if self.on_complete:
                    await self.on_complete(meta.stream_id, payload)
                return payload
        return None

    async def cleanup_stale(self):
        async with self._lock:
            stale = [sid for sid, buf in self._streams.items()
                     if buf.age_sec > self.STREAM_TIMEOUT_SEC]
            for sid in stale:
                del self._streams[sid]

    @property
    def active_streams(self) -> int:
        return len(self._streams)

    def get_stats(self) -> dict:
        return {
            "active_streams": self.active_streams,
            "stream_ids": [s.hex() for s in self._streams],
        }
