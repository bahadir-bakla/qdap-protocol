"""
Reassembler Tests (5 tests)
================================
"""

import asyncio
import time
import uuid
import pytest
from qdap.chunking.reassembler import ChunkReassembler, StreamBuffer
from qdap.chunking.chunk_qframe import make_chunk_frames


class TestChunkReassembler:

    @pytest.mark.asyncio
    async def test_single_chunk_reassemble(self):
        payload = b"Hello QDAP" * 100
        frames = make_chunk_frames(payload, chunk_size=len(payload) + 1)
        reasm = ChunkReassembler()
        result = None
        for meta, frame in frames:
            result = await reasm.process_subframes(frame.subframes)
        assert result == payload

    @pytest.mark.asyncio
    async def test_multi_chunk_reassemble(self):
        payload = b"X" * (256 * 1024)
        frames = make_chunk_frames(payload, chunk_size=64 * 1024)
        reasm = ChunkReassembler()
        result = None
        assert len(frames) == 4
        for meta, frame in frames:
            result = await reasm.process_subframes(frame.subframes)
        assert result == payload

    @pytest.mark.asyncio
    async def test_out_of_order_chunks(self):
        payload = b"OOO" * (100 * 1024)
        frames = make_chunk_frames(payload, chunk_size=64 * 1024)
        reasm = ChunkReassembler()
        for meta, frame in reversed(frames):
            result = await reasm.process_subframes(frame.subframes)
        assert result == payload

    @pytest.mark.asyncio
    async def test_multiple_concurrent_streams(self):
        reasm = ChunkReassembler()
        payload1 = b"S1" * (64 * 1024)
        payload2 = b"S2" * (64 * 1024)
        sid1 = uuid.uuid4().bytes[:8]
        sid2 = uuid.uuid4().bytes[:8]
        frames1 = make_chunk_frames(payload1, 32 * 1024, stream_id=sid1)
        frames2 = make_chunk_frames(payload2, 32 * 1024, stream_id=sid2)
        results = []
        for (_, f1), (_, f2) in zip(frames1, frames2):
            r1 = await reasm.process_subframes(f1.subframes)
            r2 = await reasm.process_subframes(f2.subframes)
            if r1: results.append(r1)
            if r2: results.append(r2)
        assert payload1 in results
        assert payload2 in results

    @pytest.mark.asyncio
    async def test_stale_cleanup(self):
        reasm = ChunkReassembler()
        fake_id = b"stale000"
        reasm._streams[fake_id] = StreamBuffer(
            stream_id=fake_id, total_chunks=5,
        )
        reasm._streams[fake_id].created_at = time.monotonic() - 31
        await reasm.cleanup_stale()
        assert fake_id not in reasm._streams
