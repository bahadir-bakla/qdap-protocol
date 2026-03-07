"""
QFrame Tests — Phase 1 Enhanced
=================================

Tests for QFrame creation, serialization/deserialization,
subframe types, seq_num tracking, create_with_encoder,
and integrity verification.
"""

import numpy as np
import pytest

from qdap.frame.qframe import (
    FrameType,
    QFrame,
    Subframe,
    SubframeType,
)
from qdap.frame.encoder import AmplitudeEncoder


class TestSubframeType:
    """SubframeType enum tests."""

    def test_type_values(self):
        assert SubframeType.DATA == 0x01
        assert SubframeType.CTRL == 0x02
        assert SubframeType.GHOST == 0x03
        assert SubframeType.PROBE == 0x04
        assert SubframeType.SYNC == 0x05

    def test_priority_map_exists(self):
        pmap = SubframeType.priority_map()
        assert SubframeType.DATA in pmap
        assert SubframeType.CTRL in pmap
        assert SubframeType.SYNC in pmap

    def test_sync_highest_priority(self):
        pmap = SubframeType.priority_map()
        assert pmap[SubframeType.SYNC] > pmap[SubframeType.DATA]


class TestSubframe:
    """Subframe creation and serialization tests."""

    def test_create_data_subframe(self):
        sf = Subframe(payload=b"hello world", type=SubframeType.DATA)
        assert sf.size_bytes == 11
        assert sf.type == SubframeType.DATA

    def test_serialize_deserialize_roundtrip(self):
        original = Subframe(payload=b"test payload", type=SubframeType.CTRL, seq_num=42)
        data = original.serialize()
        recovered, consumed = Subframe.deserialize(data)

        assert recovered.payload == original.payload
        assert recovered.type == original.type
        assert recovered.seq_num == 42
        assert consumed == len(data)

    def test_empty_payload(self):
        sf = Subframe(payload=b"", type=SubframeType.PROBE, seq_num=0)
        assert sf.size_bytes == 0
        data = sf.serialize()
        recovered, _ = Subframe.deserialize(data)
        assert recovered.payload == b""
        assert recovered.seq_num == 0

    def test_seq_num_preserved(self):
        """Sequence number must survive serialization."""
        sf = Subframe(payload=b"data", seq_num=0xDEADBEEF)
        data = sf.serialize()
        recovered, _ = Subframe.deserialize(data)
        assert recovered.seq_num == 0xDEADBEEF

    def test_max_seq_num(self):
        sf = Subframe(payload=b"x", seq_num=2**32 - 1)
        data = sf.serialize()
        recovered, _ = Subframe.deserialize(data)
        assert recovered.seq_num == 2**32 - 1


class TestQFrame:
    """QFrame creation, serialization, and integrity tests."""

    def test_create_basic_frame(self):
        subframes = [
            Subframe(payload=b"video chunk", type=SubframeType.DATA, deadline_ms=16, seq_num=1),
            Subframe(payload=b"audio chunk", type=SubframeType.DATA, deadline_ms=8, seq_num=2),
            Subframe(payload=b"cursor", type=SubframeType.DATA, deadline_ms=4, seq_num=3),
        ]
        frame = QFrame.create(subframes=subframes, session_id=0x1234)

        assert frame.subframe_count == 3
        assert frame.session_id == 0x1234
        assert len(frame.amplitude_vector) == 3

    def test_default_amplitude_normalization(self):
        """Default amplitudes should satisfy Σ|αᵢ|² = 1."""
        subframes = [
            Subframe(payload=b"a"),
            Subframe(payload=b"b"),
            Subframe(payload=b"c"),
        ]
        frame = QFrame.create(subframes=subframes)

        sum_sq = np.sum(frame.amplitude_vector ** 2)
        assert abs(sum_sq - 1.0) < 1e-6, f"Normalization failed: Σ|α|² = {sum_sq}"

    def test_send_order(self):
        """Higher amplitude → earlier in send order."""
        amplitudes = np.array([0.1, 0.9, 0.42], dtype=np.float32)
        amplitudes = amplitudes / np.linalg.norm(amplitudes)

        subframes = [Subframe(payload=b"x") for _ in range(3)]
        frame = QFrame.create(subframes=subframes, amplitude_vector=amplitudes)

        order = frame.send_order
        assert order[0] == 1

    def test_serialize_deserialize_roundtrip(self):
        """Full serialization roundtrip must preserve all fields."""
        subframes = [
            Subframe(payload=b"hello QDAP", type=SubframeType.DATA, seq_num=100),
            Subframe(payload=b"control msg", type=SubframeType.CTRL, seq_num=101),
        ]
        original = QFrame.create(
            subframes=subframes,
            session_id=0xDEADBEEF,
            frame_type=FrameType.DATA,
        )

        data = original.serialize()
        recovered = QFrame.deserialize(data)

        assert recovered.version == original.version
        assert recovered.frame_type == original.frame_type
        assert recovered.session_id == original.session_id
        assert recovered.subframe_count == original.subframe_count
        assert np.allclose(recovered.amplitude_vector, original.amplitude_vector, atol=1e-6)

        for orig_sf, recv_sf in zip(original.subframes, recovered.subframes):
            assert recv_sf.payload == orig_sf.payload
            assert recv_sf.type == orig_sf.type
            assert recv_sf.seq_num == orig_sf.seq_num

    def test_integrity_hash_tampering_detected(self):
        """Tampered data must fail integrity check."""
        subframes = [Subframe(payload=b"sensitive data", seq_num=1)]
        frame = QFrame.create(subframes=subframes)
        data = bytearray(frame.serialize())

        midpoint = len(data) // 2
        data[midpoint] ^= 0xFF

        with pytest.raises(ValueError, match="integrity"):
            QFrame.deserialize(bytes(data))

    def test_empty_frame(self):
        frame = QFrame.create(subframes=[])
        assert frame.subframe_count == 0
        assert len(frame.amplitude_vector) == 0
        assert frame.send_order == []

    def test_frame_repr(self):
        frame = QFrame.create(
            subframes=[Subframe(payload=b"test")],
            session_id=0xFF,
        )
        repr_str = repr(frame)
        assert "QFrame" in repr_str
        assert "0xff" in repr_str


class TestCreateWithEncoder:
    """Tests for QFrame.create_with_encoder."""

    def test_auto_encodes_amplitudes(self):
        subframes = [
            Subframe(payload=b"big" * 1000, deadline_ms=1000),
            Subframe(payload=b"tiny", deadline_ms=1),
        ]
        frame = QFrame.create_with_encoder(subframes=subframes)

        # Urgent subframe (tiny, 1ms deadline) should have higher amplitude
        assert frame.amplitude_vector[1] > frame.amplitude_vector[0]

    def test_normalization_holds(self):
        subframes = [
            Subframe(payload=b"a" * 100, deadline_ms=50),
            Subframe(payload=b"b" * 200, deadline_ms=100),
            Subframe(payload=b"c" * 10, deadline_ms=5),
        ]
        frame = QFrame.create_with_encoder(subframes=subframes)

        sum_sq = np.sum(frame.amplitude_vector.astype(np.float64) ** 2)
        assert abs(sum_sq - 1.0) < 1e-4

    def test_roundtrip_with_encoder(self):
        subframes = [
            Subframe(payload=b"encoded", deadline_ms=10, seq_num=99),
        ]
        frame = QFrame.create_with_encoder(subframes=subframes, session_id=0xBEEF)

        data = frame.serialize()
        recovered = QFrame.deserialize(data)
        assert recovered.subframes[0].payload == b"encoded"
        assert recovered.subframes[0].seq_num == 99
        assert recovered.session_id == 0xBEEF
