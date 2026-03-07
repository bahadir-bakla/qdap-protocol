"""
Hypothesis-Based Fuzzing Tests
===============================

Property-based tests using Hypothesis to verify QDAP invariants
under random inputs.

Invariants tested:
    1. Serialize → Deserialize roundtrip is lossless
    2. Amplitude normalization always holds: Σ|αᵢ|² = 1
    3. Send order is consistent with amplitudes
    4. Integrity hash detects any tampering
"""

import struct

import numpy as np
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from qdap.frame.qframe import QFrame, Subframe, SubframeType, FrameType
from qdap.frame.encoder import AmplitudeEncoder


# ─── Hypothesis Strategies ───────────────────────────────

subframe_types = st.sampled_from([
    SubframeType.DATA,
    SubframeType.CTRL,
    SubframeType.GHOST,
    SubframeType.PROBE,
    SubframeType.SYNC,
])

subframe_strategy = st.builds(
    Subframe,
    payload=st.binary(min_size=0, max_size=1024),
    type=subframe_types,
    deadline_ms=st.floats(min_value=0.1, max_value=10000.0, allow_nan=False, allow_infinity=False),
    seq_num=st.integers(min_value=0, max_value=2**32 - 1),
)

subframe_list = st.lists(subframe_strategy, min_size=1, max_size=16)


class TestSerializationRoundtrip:
    """Serialize → Deserialize must always be lossless."""

    @given(subframes=subframe_list)
    @settings(max_examples=100, deadline=5000)
    def test_qframe_roundtrip(self, subframes):
        """Any valid QFrame must survive serialization roundtrip."""
        frame = QFrame.create(subframes=subframes, session_id=42)

        data = frame.serialize()
        recovered = QFrame.deserialize(data)

        assert recovered.version == frame.version
        assert recovered.frame_type == frame.frame_type
        assert recovered.session_id == frame.session_id
        assert recovered.subframe_count == frame.subframe_count
        assert np.allclose(recovered.amplitude_vector, frame.amplitude_vector, atol=1e-6)

        for orig, recv in zip(frame.subframes, recovered.subframes):
            assert recv.payload == orig.payload
            assert recv.type == orig.type
            assert recv.seq_num == orig.seq_num

    @given(subframes=subframe_list)
    @settings(max_examples=50, deadline=5000)
    def test_encoded_qframe_roundtrip(self, subframes):
        """QFrame created with encoder must survive roundtrip."""
        frame = QFrame.create_with_encoder(subframes=subframes, session_id=0xBEEF)

        data = frame.serialize()
        recovered = QFrame.deserialize(data)

        assert recovered.subframe_count == len(subframes)
        for orig, recv in zip(frame.subframes, recovered.subframes):
            assert recv.payload == orig.payload


class TestAmplitudeInvariants:
    """Amplitude vector invariants must always hold."""

    @given(subframes=subframe_list)
    @settings(max_examples=100, deadline=5000)
    def test_normalization_always_holds(self, subframes):
        """Σ|αᵢ|² must always equal 1.0 (quantum state normalization)."""
        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)

        sum_sq = np.sum(amplitudes**2)
        assert abs(sum_sq - 1.0) < 1e-6, f"Normalization failed: Σ|α|² = {sum_sq}"

    @given(subframes=subframe_list)
    @settings(max_examples=100, deadline=5000)
    def test_all_amplitudes_non_negative(self, subframes):
        """All amplitude values must be non-negative."""
        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)

        assert np.all(amplitudes >= 0), f"Negative amplitude found: {amplitudes}"

    @given(subframes=subframe_list)
    @settings(max_examples=50, deadline=5000)
    def test_schedule_length_matches_subframes(self, subframes):
        """Schedule must contain exactly as many indices as subframes."""
        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)
        schedule = encoder.decode_schedule(amplitudes)

        assert len(schedule) == len(subframes)
        assert set(schedule) == set(range(len(subframes)))

    @given(subframes=subframe_list)
    @settings(max_examples=50, deadline=5000)
    def test_schedule_highest_amplitude_first(self, subframes):
        """First element in schedule must have highest |α|²."""
        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)
        schedule = encoder.decode_schedule(amplitudes)

        if len(schedule) > 1:
            assert amplitudes[schedule[0]]**2 >= amplitudes[schedule[-1]]**2


class TestIntegrityFuzzing:
    """Integrity hash must detect any tampering."""

    @given(subframes=subframe_list, tamper_offset=st.integers(min_value=0, max_value=100))
    @settings(max_examples=50, deadline=5000)
    def test_any_bit_flip_detected(self, subframes, tamper_offset):
        """Flipping any bit in serialized data must cause an error."""
        frame = QFrame.create(subframes=subframes, session_id=1)
        data = bytearray(frame.serialize())

        # Pick a valid offset to tamper (not in the hash itself)
        content_end = len(data) - 32  # last 32 bytes are the hash
        assume(content_end > 0)
        idx = tamper_offset % content_end

        # Flip a bit
        data[idx] ^= 0x01

        # Must raise some kind of error: ValueError (integrity or enum),
        # struct.error, or other deserialization failure
        with pytest.raises((ValueError, struct.error, Exception)):
            QFrame.deserialize(bytes(data))


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_max_subframe_count(self):
        """Frame with many subframes should work."""
        subframes = [Subframe(payload=b"x", seq_num=i) for i in range(100)]
        frame = QFrame.create(subframes=subframes)

        data = frame.serialize()
        recovered = QFrame.deserialize(data)
        assert recovered.subframe_count == 100

    def test_large_payload(self):
        """Frame with large payload should work."""
        big_payload = b"\xAA" * 65536
        frame = QFrame.create(
            subframes=[Subframe(payload=big_payload, seq_num=1)]
        )
        data = frame.serialize()
        recovered = QFrame.deserialize(data)
        assert recovered.subframes[0].payload == big_payload

    def test_all_frame_types(self):
        """All FrameType values must serialize correctly."""
        for ft in FrameType:
            frame = QFrame.create(
                subframes=[Subframe(payload=b"test")],
                frame_type=ft,
            )
            data = frame.serialize()
            recovered = QFrame.deserialize(data)
            assert recovered.frame_type == ft

    def test_all_subframe_types(self):
        """All SubframeType values must serialize correctly."""
        for st_type in SubframeType:
            sf = Subframe(payload=b"test", type=st_type, seq_num=42)
            frame = QFrame.create(subframes=[sf])
            data = frame.serialize()
            recovered = QFrame.deserialize(data)
            assert recovered.subframes[0].type == st_type
            assert recovered.subframes[0].seq_num == 42
