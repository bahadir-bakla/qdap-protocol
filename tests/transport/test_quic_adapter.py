"""
QUIC Adapter Tests
=====================

Tests for QDAP QUIC transport adapter.
"""

import asyncio
import pytest

from qdap.transport.quic.adapter import QDAPQUICAdapter, generate_self_signed_cert
from qdap.frame.qframe import QFrame, Subframe, SubframeType


class TestQUICAdapter:

    def test_adapter_creation(self):
        adapter = QDAPQUICAdapter()
        assert not adapter.is_healthy()
        assert adapter._frames_sent == 0

    def test_self_signed_cert_generation(self, tmp_path):
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.endswith("cert.pem")
        assert key_path.endswith("key.pem")
        import os
        assert os.path.exists(cert_path)
        assert os.path.exists(key_path)

    def test_transport_stats_initial(self):
        adapter = QDAPQUICAdapter()
        stats = adapter.get_transport_stats()
        assert stats["type"] == "quic"
        assert stats["frames_sent"] == 0
        assert stats["bytes_sent"] == 0

    @pytest.mark.asyncio
    async def test_send_frame_without_protocol(self):
        """send_frame works in offline mode (counts but doesn't send)."""
        adapter = QDAPQUICAdapter()
        sf = Subframe(payload=b"hello quic", type=SubframeType.DATA, deadline_ms=50.0)
        frame = QFrame.create_with_encoder([sf])
        await adapter.send_frame(frame)
        assert adapter._frames_sent == 1
        assert adapter._bytes_sent > 0

    @pytest.mark.asyncio
    async def test_close_sets_unhealthy(self):
        adapter = QDAPQUICAdapter()
        adapter._healthy = True
        assert adapter.is_healthy()
        await adapter.close()
        assert not adapter.is_healthy()

    def test_handle_stream_data_parses_frame(self):
        """Test internal frame parsing from stream data."""
        adapter = QDAPQUICAdapter()
        sf = Subframe(payload=b"test data", type=SubframeType.DATA, deadline_ms=10.0)
        frame = QFrame.create_with_encoder([sf])
        data = frame.serialize()

        # Create length-prefixed message
        import struct
        msg = struct.pack(">I", len(data)) + data
        adapter._handle_stream_data(msg)

        assert adapter._frames_received == 1
        assert not adapter._recv_queue.empty()

    def test_data_stream_id(self):
        assert QDAPQUICAdapter.DATA_STREAM_ID == 0
