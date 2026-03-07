"""
Adaptive Chunker Tests (8 tests)
====================================
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from qdap.chunking.adaptive_chunker import AdaptiveChunker
from qdap.chunking.strategy import ChunkStrategy


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.send_frame = AsyncMock()
    adapter.get_transport_stats = MagicMock(return_value={"bytes_sent": 0, "frames_sent": 0})
    return adapter


@pytest.fixture
def mock_scheduler():
    sched = MagicMock()
    sched.has_enough_data = True
    sched._last_energy_bands = {"low": 0.75, "mid": 0.15, "high": 0.10}
    sched.chunk_size_for = MagicMock(return_value=256 * 1024)
    sched.chunk_strategy_name = "LARGE (256KB) — bulk"
    sched.observe_packet_size = MagicMock()
    return sched


@pytest.fixture
def chunker(mock_adapter, mock_scheduler):
    return AdaptiveChunker(mock_adapter, mock_scheduler)


class TestAdaptiveChunker:

    @pytest.mark.asyncio
    async def test_small_payload_single_frame(self, chunker, mock_adapter):
        payload = b"X" * (16 * 1024)
        result = await chunker.send(payload)
        assert result["mode"] == "single"
        assert result["frames"] == 1
        mock_adapter.send_frame.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_payload_chunked(self, chunker, mock_adapter):
        payload = b"Y" * (1024 * 1024)
        result = await chunker.send(payload)
        assert result["mode"] == "adaptive_batch"
        assert result["n_chunks"] > 1
        assert mock_adapter.send_frame.call_count == result["n_batches"]

    @pytest.mark.asyncio
    async def test_chunk_size_from_scheduler(self, chunker, mock_scheduler):
        payload = b"Z" * (2 * 1024 * 1024)
        await chunker.send(payload)
        mock_scheduler.chunk_size_for.assert_called_once_with(len(payload))

    @pytest.mark.asyncio
    async def test_no_ack_bytes(self, chunker, mock_adapter):
        payload = b"A" * (1024 * 1024)
        await chunker.send(payload)
        for call in mock_adapter.method_calls:
            assert "ack" not in str(call).lower()

    @pytest.mark.asyncio
    async def test_chunk_ordering(self, chunker, mock_adapter):
        payload = b"B" * (512 * 1024)
        await chunker.send(payload, deadline_ms=50.0)
        assert mock_adapter.send_frame.call_count > 1

    @pytest.mark.asyncio
    async def test_stats_updated(self, chunker):
        payload = b"C" * (1024 * 1024)
        await chunker.send(payload)
        stats = chunker.get_stats()
        assert stats["total_payloads"] == 1
        assert stats["total_bytes"] == len(payload)

    @pytest.mark.asyncio
    async def test_exact_boundary_payload(self, chunker, mock_adapter):
        payload = b"D" * (32 * 1024)
        result = await chunker.send(payload)
        assert result["mode"] == "adaptive_batch"

    @pytest.mark.asyncio
    async def test_100mb_payload(self, chunker, mock_adapter, mock_scheduler):
        mock_scheduler.chunk_size_for.return_value = 1024 * 1024
        payload = b"E" * (100 * 1024 * 1024)
        result = await chunker.send(payload)
        assert result["mode"] == "adaptive_batch"
        assert result["n_chunks"] == 100


class TestAdaptiveChunkerFixes:

    @pytest.mark.asyncio
    async def test_100mb_uses_jumbo_without_warmup(self, mock_adapter, mock_scheduler):
        """Fix 1: No warm-up + 100MB → JUMBO."""
        mock_scheduler.has_enough_data = False
        mock_scheduler.chunk_size_for = MagicMock(return_value=1024 * 1024)
        chunker = AdaptiveChunker(mock_adapter, mock_scheduler)
        payload = b"F" * (100 * 1024 * 1024)
        result = await chunker.send(payload)
        assert result["mode"] == "adaptive_batch"
        assert result["chunk_size"] == 1024 * 1024
        assert result["n_chunks"] == 100

    @pytest.mark.asyncio
    async def test_warmup_trains_scheduler(self, mock_adapter, mock_scheduler):
        """Fix 2: Warmup calls observe_packet_size."""
        chunker = AdaptiveChunker(mock_adapter, mock_scheduler)
        await chunker.warmup(sample_payload_size=1024 * 1024, n_samples=128)
        assert mock_scheduler.observe_packet_size.call_count == 128

    def test_payload_size_default_small(self):
        s = ChunkStrategy._payload_size_default(16 * 1024)
        assert s == ChunkStrategy.SMALL

    def test_payload_size_default_large(self):
        s = ChunkStrategy._payload_size_default(5 * 1024 * 1024)
        assert s == ChunkStrategy.LARGE

    def test_payload_size_default_jumbo(self):
        s = ChunkStrategy._payload_size_default(100 * 1024 * 1024)
        assert s == ChunkStrategy.JUMBO

    @pytest.mark.asyncio
    async def test_warmup_from_history(self, mock_adapter, mock_scheduler):
        chunker = AdaptiveChunker(mock_adapter, mock_scheduler)
        history = [1024] * 50 + [1024 * 1024] * 50
        await chunker.warmup_from_history(history)
        assert mock_scheduler.observe_packet_size.call_count == 100
