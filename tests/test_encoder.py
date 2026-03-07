"""
Amplitude Encoder Tests
=======================

Tests for quantum-inspired amplitude encoding,
normalization (Born rule), and priority scheduling.
"""

import numpy as np
import pytest

from qdap.frame.encoder import AmplitudeEncoder
from qdap.frame.qframe import Subframe, SubframeType


class TestAmplitudeEncoder:
    """AmplitudeEncoder unit tests."""

    def setup_method(self):
        self.encoder = AmplitudeEncoder()

    def test_normalization_constraint(self):
        """Amplitude vector must satisfy Σ|αᵢ|² = 1."""
        subframes = [
            Subframe(payload=b"x" * 100, type=SubframeType.DATA, deadline_ms=16),
            Subframe(payload=b"y" * 50, type=SubframeType.DATA, deadline_ms=8),
            Subframe(payload=b"z" * 10, type=SubframeType.DATA, deadline_ms=4),
        ]
        amplitudes = self.encoder.encode(subframes)

        sum_sq = np.sum(amplitudes ** 2)
        assert abs(sum_sq - 1.0) < 1e-9, f"Normalization failed: Σ|α|² = {sum_sq}"

    def test_shorter_deadline_higher_amplitude(self):
        """Subframe with shorter deadline should get higher amplitude."""
        subframes = [
            Subframe(payload=b"x" * 100, deadline_ms=1000),  # relaxed
            Subframe(payload=b"y" * 100, deadline_ms=1),       # urgent
        ]
        amplitudes = self.encoder.encode(subframes)

        assert amplitudes[1] > amplitudes[0], (
            f"Urgent subframe should have higher amplitude: {amplitudes}"
        )

    def test_smaller_size_higher_amplitude(self):
        """Smaller payloads (sensor pings) should get higher priority."""
        subframes = [
            Subframe(payload=b"x" * 10000, deadline_ms=100),  # big
            Subframe(payload=b"y" * 10, deadline_ms=100),       # small
        ]
        amplitudes = self.encoder.encode(subframes)

        assert amplitudes[1] > amplitudes[0], (
            f"Smaller payload should have higher amplitude: {amplitudes}"
        )

    def test_type_priority(self):
        """SYNC type should have higher priority than PROBE."""
        subframes = [
            Subframe(payload=b"probe", type=SubframeType.PROBE, deadline_ms=100),
            Subframe(payload=b"syncc", type=SubframeType.SYNC, deadline_ms=100),
        ]
        amplitudes = self.encoder.encode(subframes)

        assert amplitudes[1] > amplitudes[0], (
            f"SYNC should outweigh PROBE: {amplitudes}"
        )

    def test_decode_schedule_ordering(self):
        """decode_schedule should return indices sorted by |α|² descending."""
        amplitudes = np.array([0.2, 0.8, 0.5])
        amplitudes = amplitudes / np.linalg.norm(amplitudes)  # normalize

        order = self.encoder.decode_schedule(amplitudes)

        # Index 1 (0.8) should be first
        assert order[0] == 1
        # Index 2 (0.5) should be second
        assert order[1] == 2
        # Index 0 (0.2) should be last
        assert order[2] == 0

    def test_empty_subframes(self):
        """Empty input should return empty amplitude vector."""
        amplitudes = self.encoder.encode([])
        assert len(amplitudes) == 0

    def test_single_subframe(self):
        """Single subframe should get amplitude = 1.0."""
        subframes = [Subframe(payload=b"only one")]
        amplitudes = self.encoder.encode(subframes)

        assert len(amplitudes) == 1
        assert abs(amplitudes[0] - 1.0) < 1e-9

    def test_decode_schedule_empty(self):
        """Empty amplitude vector → empty schedule."""
        assert self.encoder.decode_schedule(np.array([])) == []
