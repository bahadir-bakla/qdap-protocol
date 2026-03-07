"""
Chunk Strategy Tests (5 tests)
==================================
"""

import pytest
from qdap.chunking.strategy import ChunkStrategy


class TestChunkStrategy:

    def test_high_freq_gives_micro(self):
        s = ChunkStrategy.from_energy_bands(low=0.1, mid=0.2, high=0.7, payload_size=1024*1024)
        assert s == ChunkStrategy.MICRO

    def test_low_freq_large_payload_gives_jumbo(self):
        s = ChunkStrategy.from_energy_bands(low=0.8, mid=0.15, high=0.05, payload_size=50*1024*1024)
        assert s == ChunkStrategy.JUMBO

    def test_low_freq_medium_payload_gives_large(self):
        s = ChunkStrategy.from_energy_bands(low=0.75, mid=0.15, high=0.10, payload_size=5*1024*1024)
        assert s == ChunkStrategy.LARGE

    def test_mixed_traffic_medium(self):
        s = ChunkStrategy.from_energy_bands(low=0.33, mid=0.33, high=0.34, payload_size=512*1024)
        assert s in (ChunkStrategy.MEDIUM, ChunkStrategy.SMALL)

    def test_describe_returns_string(self):
        for strategy in ChunkStrategy:
            assert len(strategy.describe()) > 0
