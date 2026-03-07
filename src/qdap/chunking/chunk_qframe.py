"""
Chunk QFrame Wire Format
============================

Splits large payloads into chunk QFrames with metadata headers.
Each chunk QFrame = [CTRL subframe (metadata)] + [DATA subframe (chunk)].
"""

import struct
import uuid
from dataclasses import dataclass
from typing import Optional

from qdap.frame.qframe import QFrame, Subframe, SubframeType

CHUNK_HEADER_SIZE = 20
CHUNK_FLAG_FIRST = 0x01
CHUNK_FLAG_LAST  = 0x02
CHUNK_FLAG_ONLY  = 0x03


@dataclass
class ChunkMetadata:
    stream_id: bytes       # 8 byte unique ID
    chunk_index: int       # 0-based
    total_chunks: int
    is_first: bool
    is_last: bool

    def to_bytes(self) -> bytes:
        flags = 0
        if self.is_first: flags |= CHUNK_FLAG_FIRST
        if self.is_last:  flags |= CHUNK_FLAG_LAST
        return struct.pack(">8sIII", self.stream_id, self.chunk_index,
                           self.total_chunks, flags)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'ChunkMetadata':
        stream_id, idx, total, flags = struct.unpack(">8sIII", data[:20])
        return cls(
            stream_id=stream_id, chunk_index=idx, total_chunks=total,
            is_first=bool(flags & CHUNK_FLAG_FIRST),
            is_last=bool(flags & CHUNK_FLAG_LAST),
        )


def make_chunk_frames(
    payload: bytes,
    chunk_size: int,
    deadline_ms: float = 100.0,
    stream_id: Optional[bytes] = None,
) -> list:
    """
    Split large payload into chunk QFrame list.

    Each QFrame has 2 subframes:
    - CTRL: ChunkMetadata (20 bytes)
    - DATA: chunk payload
    """
    if stream_id is None:
        stream_id = uuid.uuid4().bytes[:8]

    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    total = len(chunks)
    frames = []

    for idx, chunk in enumerate(chunks):
        meta = ChunkMetadata(
            stream_id=stream_id, chunk_index=idx, total_chunks=total,
            is_first=(idx == 0), is_last=(idx == total - 1),
        )
        sf_meta = Subframe(
            payload=meta.to_bytes(), type=SubframeType.CTRL,
            deadline_ms=deadline_ms * 2,
        )
        sf_data = Subframe(
            payload=chunk, type=SubframeType.DATA,
            deadline_ms=deadline_ms,
        )
        frame = QFrame.create_with_encoder([sf_meta, sf_data])
        frames.append((meta, frame))

    return frames


# ─── Batch Wire Format ───────────────────────────────────
# Groups N chunks into 1 QFrame → 1 SHA3-256 hash
# Batch header: 28 bytes
# [stream_id(8)][batch_index(4)][total_batches(4)]
# [chunks_in_batch(4)][first_chunk_idx(4)][flags(4)]

BATCH_HEADER_SIZE = 28
BATCH_FLAG_FIRST = 0x01
BATCH_FLAG_LAST  = 0x02


@dataclass
class BatchMetadata:
    stream_id: bytes
    batch_index: int
    total_batches: int
    chunks_in_batch: int
    first_chunk_idx: int
    is_first: bool
    is_last: bool

    def to_bytes(self) -> bytes:
        flags = 0
        if self.is_first: flags |= BATCH_FLAG_FIRST
        if self.is_last:  flags |= BATCH_FLAG_LAST
        return struct.pack(
            ">8sIIIII", self.stream_id, self.batch_index,
            self.total_batches, self.chunks_in_batch,
            self.first_chunk_idx, flags,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'BatchMetadata':
        sid, bidx, total, n_chunks, first_idx, flags = struct.unpack(
            ">8sIIIII", data[:BATCH_HEADER_SIZE]
        )
        return cls(
            stream_id=sid, batch_index=bidx, total_batches=total,
            chunks_in_batch=n_chunks, first_chunk_idx=first_idx,
            is_first=bool(flags & BATCH_FLAG_FIRST),
            is_last=bool(flags & BATCH_FLAG_LAST),
        )


def make_batch_frames(
    payload: bytes,
    chunk_size: int,
    batch_size: int,
    deadline_ms: float = 100.0,
    stream_id: Optional[bytes] = None,
) -> list:
    """
    Group N chunks into 1 QFrame → 1 SHA3-256 hash.

    10MB, chunk=256KB, batch=8 → 40 chunks → 5 QFrames (8× less overhead).
    """
    if stream_id is None:
        stream_id = uuid.uuid4().bytes[:8]

    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]
    total_chunks = len(chunks)

    batches = [chunks[i:i + batch_size] for i in range(0, total_chunks, batch_size)]
    total_batches = len(batches)

    frames = []
    chunk_cursor = 0

    for batch_idx, batch_chunks in enumerate(batches):
        batch_payload = b"".join(batch_chunks)

        meta = BatchMetadata(
            stream_id=stream_id, batch_index=batch_idx,
            total_batches=total_batches, chunks_in_batch=len(batch_chunks),
            first_chunk_idx=chunk_cursor,
            is_first=(batch_idx == 0), is_last=(batch_idx == total_batches - 1),
        )

        sf_meta = Subframe(
            payload=meta.to_bytes(), type=SubframeType.CTRL,
            deadline_ms=deadline_ms * 2,
        )
        sf_data = Subframe(
            payload=batch_payload, type=SubframeType.DATA,
            deadline_ms=deadline_ms,
        )

        frame = QFrame.create_with_encoder([sf_meta, sf_data])
        frames.append((meta, frame))
        chunk_cursor += len(batch_chunks)

    return frames
