# tests/test_qframe_rust.py

import pytest
import struct
from qdap._rust_bridge import qframe_serialize, qframe_deserialize, qframe_peek_header

class TestQFrameRust:

    def test_basic_roundtrip(self):
        payload = b"Hello QDAP Rust!" * 100
        wire    = qframe_serialize(payload, priority=100, deadline_ms=500.0,
                                   sequence_number=42, frame_type=0)
        parsed_payload, priority, deadline, seq, ftype, hash_valid = \
            qframe_deserialize(wire)

        assert parsed_payload == payload
        assert priority       == 100
        assert deadline       == 500.0
        assert seq            == 42
        assert ftype          == 0
        assert hash_valid     is True

    def test_tampered_payload_invalid(self):
        payload  = b"secret data"
        wire     = bytearray(qframe_serialize(payload, 0, 0.0, 0, 0))
        wire[61] ^= 0xFF  # payload bozuldu
        _, _, _, _, _, hash_valid = qframe_deserialize(bytes(wire))
        assert hash_valid is False

    def test_too_short_raises(self):
        with pytest.raises((ValueError, Exception)):
            qframe_deserialize(b"short")

    def test_wrong_magic_raises(self):
        payload = b"test"
        wire    = bytearray(qframe_serialize(payload, 0, 0.0, 0, 0))
        wire[0] = 0xFF  # magic bozuldu
        with pytest.raises((ValueError, Exception)):
            qframe_deserialize(bytes(wire))

    def test_empty_payload(self):
        wire = qframe_serialize(b"", 0, 0.0, 0, 0)
        payload, _, _, _, _, valid = qframe_deserialize(wire)
        assert payload == b""
        assert valid

    def test_large_payload(self):
        import os
        payload = os.urandom(1024 * 1024)  # 1MB
        wire    = qframe_serialize(payload, 0, 0.0, 0, 0)
        parsed, _, _, _, _, valid = qframe_deserialize(wire)
        assert parsed == payload
        assert valid

    def test_peek_header(self):
        payload = b"X" * 64 * 1024
        wire    = qframe_serialize(payload, priority=999, deadline_ms=2.0,
                                   sequence_number=0, frame_type=0)
        length, priority, deadline, ftype = qframe_peek_header(wire)
        assert length   == len(payload)
        assert priority == 999
        assert deadline == 2.0


class TestQFTSchedulerRust:

    def test_small_payload_high_loss_small_chunk(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(512, 100.0, 0.15)
        assert chunk <= 16 * 1024  # küçük chunk

    def test_large_payload_no_loss_large_chunk(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(10 * 1024 * 1024, 2.0, 0.0)
        assert chunk >= 256 * 1024  # büyük chunk

    def test_returns_valid_types(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(65536, 20.0, 0.01)
        assert isinstance(chunk, int)
        assert isinstance(strategy, int)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0
