"""
Batch Frame Tests (8 tests)
================================
"""

import pytest
from qdap.chunking.chunk_qframe import make_batch_frames, BatchMetadata
from qdap.chunking.batch_config import BatchConfig
from qdap.chunking.reassembler import ChunkReassembler


class TestBatchConfig:

    def test_small_payload_no_batch(self):
        """4 chunks or less → batch_size=1."""
        size = BatchConfig.for_payload(payload_size=512 * 1024, chunk_size=256 * 1024)
        assert size == 1

    def test_10mb_batch_size(self):
        """10MB / 256KB = 40 chunks → batch=8."""
        size = BatchConfig.for_payload(payload_size=10 * 1024 * 1024, chunk_size=256 * 1024)
        assert size == BatchConfig.DEFAULT_BATCH

    def test_100mb_batch_size(self):
        """100MB / 1MB = 100 chunks → batch >= 1."""
        size = BatchConfig.for_payload(payload_size=100 * 1024 * 1024, chunk_size=1024 * 1024)
        assert size >= 1


class TestMakeBatchFrames:

    def test_10mb_frame_count(self):
        """10MB / 256KB chunk / 8 batch = 5 QFrames."""
        payload = b"X" * (10 * 1024 * 1024)
        frames = make_batch_frames(payload=payload, chunk_size=256 * 1024, batch_size=8)
        assert len(frames) == 5

    def test_batch_metadata_correct(self):
        payload = b"Z" * (10 * 1024 * 1024)
        frames = make_batch_frames(payload, 256 * 1024, 8)
        first_meta, _ = frames[0]
        last_meta, _ = frames[-1]
        assert first_meta.is_first
        assert last_meta.is_last
        assert first_meta.batch_index == 0
        assert last_meta.batch_index == len(frames) - 1
        assert first_meta.total_batches == len(frames)

    def test_hash_count_reduction(self):
        """40 chunks → 5 QFrames = 8× reduction."""
        payload = b"B" * (10 * 1024 * 1024)
        frames = make_batch_frames(payload, 256 * 1024, 8)
        assert len(frames) == 5
        assert 40 / len(frames) == 8.0


class TestBatchReassembler:

    @pytest.mark.asyncio
    async def test_batch_reassemble_10mb(self):
        payload = b"C" * (10 * 1024 * 1024)
        frames = make_batch_frames(payload, 256 * 1024, 8)
        reasm = ChunkReassembler()
        result = None
        for meta, frame in frames:
            result = await reasm.process_subframes(frame.subframes)
        assert result == payload

    @pytest.mark.asyncio
    async def test_batch_out_of_order(self):
        payload = b"D" * (5 * 1024 * 1024)
        frames = make_batch_frames(payload, 256 * 1024, 4)
        reasm = ChunkReassembler()
        result = None
        for meta, frame in reversed(frames):
            result = await reasm.process_subframes(frame.subframes)
        assert result == payload
