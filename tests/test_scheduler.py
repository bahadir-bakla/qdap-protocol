"""
QFT Scheduler Tests — Phase 1 Enhanced
========================================

Tests for QFT-based traffic analysis, energy band computation,
strategy selection, spectral report, and stress scenarios.
"""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from qdap.scheduler.qft_scheduler import Packet, QFTScheduler, TrafficSpectrum
from qdap.scheduler.strategies import (
    AdaptiveHybridStrategy,
    BulkTransferStrategy,
    LatencyFirstStrategy,
)


class TestPacket:
    def test_size_auto_computed(self):
        p = Packet(payload=b"hello")
        assert p.size_bytes == 5

    def test_empty_packet(self):
        p = Packet(payload=b"")
        assert p.size_bytes == 0


class TestEnergyBands:
    def test_energy_bands_sum_to_one(self):
        scheduler = QFTScheduler(window_size=64)
        magnitudes = np.random.rand(64)
        bands = scheduler._compute_energy_bands(magnitudes)
        total = bands["low"] + bands["mid"] + bands["high"]
        assert abs(total - 1.0) < 1e-6

    def test_zero_magnitudes(self):
        scheduler = QFTScheduler(window_size=64)
        bands = scheduler._compute_energy_bands(np.zeros(64))
        assert abs(bands["low"] - 0.33) < 0.02

    @given(magnitudes=st.lists(
        st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False),
        min_size=10, max_size=128,
    ))
    @settings(max_examples=50, deadline=5000)
    def test_energy_bands_always_valid(self, magnitudes):
        """Energy bands must always be in [0,1] and sum to ~1."""
        scheduler = QFTScheduler(window_size=len(magnitudes))
        arr = np.array(magnitudes, dtype=np.float64)
        bands = scheduler._compute_energy_bands(arr)

        for key in ["low", "mid", "high"]:
            assert 0.0 <= bands[key] <= 1.0 + 1e-6

        total = bands["low"] + bands["mid"] + bands["high"]
        assert abs(total - 1.0) < 0.02


class TestQFTScheduler:
    def test_observe_packets(self):
        scheduler = QFTScheduler(window_size=8)
        for i in range(8):
            scheduler.observe(Packet(payload=b"x" * (i + 1) * 10))
        assert len(scheduler.packet_history) == 8

    def test_analyze_traffic_returns_spectrum(self):
        scheduler = QFTScheduler(window_size=8)
        for i in range(8):
            scheduler.observe(Packet(payload=b"x" * (i + 1) * 100))
        spectrum = scheduler.analyze_traffic()
        assert isinstance(spectrum, TrafficSpectrum)
        assert len(spectrum.frequencies) == 8
        assert len(spectrum.magnitudes) == 8

    def test_default_strategy_is_adaptive(self):
        scheduler = QFTScheduler()
        strategy = scheduler.current_strategy()
        assert isinstance(strategy, AdaptiveHybridStrategy)

    def test_schedule_preserves_all_packets(self):
        scheduler = QFTScheduler(window_size=8)
        packets = [
            Packet(payload=b"a", deadline_ms=100),
            Packet(payload=b"bb", deadline_ms=50),
            Packet(payload=b"ccc", deadline_ms=10),
        ]
        result = scheduler.schedule(packets)
        assert len(result) == 3

    def test_has_enough_data(self):
        scheduler = QFTScheduler(window_size=4)
        assert not scheduler.has_enough_data
        for i in range(4):
            scheduler.observe(Packet(payload=b"x" * 10))
        assert scheduler.has_enough_data

    def test_strategy_name(self):
        scheduler = QFTScheduler()
        assert scheduler.strategy_name == "ADAPTIVE_HYBRID"

    def test_spectrum_report_before_data(self):
        scheduler = QFTScheduler(window_size=64)
        report = scheduler.get_spectrum_report()
        assert "Not enough data" in report

    def test_spectrum_report_with_data(self):
        scheduler = QFTScheduler(window_size=8)
        for i in range(8):
            scheduler.observe(Packet(payload=b"x" * (i * 100 + 10)))
        report = scheduler.get_spectrum_report()
        assert "QFT Spectral" in report
        assert "Strategy" in report

    def test_stress_10k_packets(self):
        """10K packets should be processed without errors."""
        scheduler = QFTScheduler(window_size=64)
        for i in range(10000):
            size = (i % 100 + 1) * 10
            scheduler.observe(Packet(payload=b"x" * size))

        # Should have analyzed multiple windows
        assert scheduler.has_enough_data
        assert scheduler._current_spectrum is not None


class TestStrategies:
    def test_bulk_sorts_by_size_desc(self):
        strategy = BulkTransferStrategy()
        packets = [
            Packet(payload=b"x" * 10),
            Packet(payload=b"x" * 1000),
            Packet(payload=b"x" * 100),
        ]
        result = strategy.sort(packets)
        sizes = [p.size_bytes for p in result]
        assert sizes == sorted(sizes, reverse=True)

    def test_latency_first_sorts_by_deadline_asc(self):
        strategy = LatencyFirstStrategy()
        packets = [
            Packet(payload=b"a", deadline_ms=100),
            Packet(payload=b"b", deadline_ms=10),
            Packet(payload=b"c", deadline_ms=50),
        ]
        result = strategy.sort(packets)
        deadlines = [p.deadline_ms for p in result]
        assert deadlines == sorted(deadlines)

    def test_adaptive_hybrid_preserves_all(self):
        strategy = AdaptiveHybridStrategy(low_weight=0.3, high_weight=0.7)
        packets = [
            Packet(payload=b"a", deadline_ms=100),
            Packet(payload=b"b", deadline_ms=10),
            Packet(payload=b"c", deadline_ms=50),
        ]
        result = strategy.sort(packets)
        assert len(result) == 3

    def test_empty_queue(self):
        for strategy in [BulkTransferStrategy(), LatencyFirstStrategy(), AdaptiveHybridStrategy()]:
            assert strategy.sort([]) == []

    @given(
        sizes=st.lists(st.integers(min_value=1, max_value=1000), min_size=1, max_size=50),
        deadlines=st.lists(st.floats(min_value=1, max_value=1000, allow_nan=False), min_size=1, max_size=50),
    )
    @settings(max_examples=30, deadline=5000)
    def test_strategies_preserve_packet_count(self, sizes, deadlines):
        """All strategies must output same number of packets as input."""
        min_len = min(len(sizes), len(deadlines))
        packets = [
            Packet(payload=b"x" * sizes[i], deadline_ms=deadlines[i])
            for i in range(min_len)
        ]

        for strategy in [BulkTransferStrategy(), LatencyFirstStrategy(), AdaptiveHybridStrategy()]:
            result = strategy.sort(packets)
            assert len(result) == len(packets)
