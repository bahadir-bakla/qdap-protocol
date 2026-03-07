"""
End-to-End Integration Tests
==============================

Tests for QDAPServer + QDAPClient over real TCP connections.
Verifies the full pipeline: encoding → framing → TCP → parsing → delivery.
"""

from __future__ import annotations

import asyncio
import socket
import pytest
import numpy as np

from qdap.frame.qframe import QFrame, Subframe, SubframeType, FrameType
from qdap.frame.encoder import AmplitudeEncoder
from qdap.server import QDAPServer, QDAPClient


def _find_free_port() -> int:
    """Find a free TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestServerClientBasic:
    """Basic server-client communication tests."""

    @pytest.mark.asyncio
    async def test_server_starts_and_stops(self):
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port, shared_secret=b"test")
        await srv.start()
        assert srv.is_running
        await srv.stop()

    @pytest.mark.asyncio
    async def test_client_connects(self):
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        async with QDAPClient("127.0.0.1", port) as client:
            assert client.is_connected

        await srv.stop()

    @pytest.mark.asyncio
    async def test_send_single_frame(self):
        """Send a single QFrame and verify server receives it."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        subframes = [Subframe(payload=b"hello QDAP", seq_num=1)]
        frame = QFrame.create(subframes=subframes)
        await client.send_frame(frame)

        await asyncio.sleep(0.1)

        received = srv.drain()
        assert len(received) == 1
        assert received[0].subframes[0].payload == b"hello QDAP"
        assert received[0].subframes[0].seq_num == 1

        await client.close()
        await srv.stop()


class TestMultiframeDelivery:
    """Test multiframe sending with priority ordering."""

    @pytest.mark.asyncio
    async def test_send_multiframe_explicit_priorities(self):
        """Send 3 payloads with explicit priorities, verify order."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        data_a = b"HIGH priority payload"
        data_b = b"MEDIUM priority payload"
        data_c = b"LOW priority payload"

        frame = await client.send_multiframe(
            payloads=[data_a, data_b, data_c],
            priorities=[0.8, 0.5, 0.3],
        )

        await asyncio.sleep(0.1)

        # Verify send order: highest priority first
        order = frame.send_order
        assert order[0] == 0  # data_a has highest priority (0.8)

        # Verify server received it
        payloads = srv.drain_payloads()
        assert len(payloads) == 3
        assert payloads[0] == data_a  # Highest priority first

        await client.close()
        await srv.stop()

    @pytest.mark.asyncio
    async def test_send_multiframe_auto_encoding(self):
        """Send payloads with auto-computed amplitudes."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        frame = await client.send_multiframe(
            payloads=[b"video" * 100, b"audio" * 10, b"ctrl"],
            deadline_ms=[16.0, 8.0, 4.0],
        )

        await asyncio.sleep(0.1)

        # Verify normalization
        sum_sq = np.sum(frame.amplitude_vector**2)
        assert abs(sum_sq - 1.0) < 1e-4

        received = srv.drain()
        assert len(received) == 1
        assert received[0].subframe_count == 3

        await client.close()
        await srv.stop()

    @pytest.mark.asyncio
    async def test_multiple_sequential_sends(self):
        """Send multiple frames sequentially."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        for i in range(5):
            await client.send_multiframe(payloads=[f"message_{i}".encode()])

        await asyncio.sleep(0.2)

        received = srv.drain()
        assert len(received) == 5

        await client.close()
        await srv.stop()


class TestIntegrity:
    """Test integrity verification over the wire."""

    @pytest.mark.asyncio
    async def test_frame_integrity_preserved(self):
        """Frame integrity hash must survive TCP transport."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        original_payload = b"integrity test data" * 50
        subframes = [Subframe(payload=original_payload, seq_num=42)]
        frame = QFrame.create(subframes=subframes, session_id=0xCAFE)

        await client.send_frame(frame)
        await asyncio.sleep(0.1)

        received = srv.drain()
        assert len(received) == 1
        assert received[0].subframes[0].payload == original_payload
        assert received[0].session_id == 0xCAFE

        await client.close()
        await srv.stop()


class TestMultiClient:
    """Test multiple concurrent clients."""

    @pytest.mark.asyncio
    async def test_two_clients(self):
        """Two clients sending simultaneously."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        async with QDAPClient("127.0.0.1", port) as c1, \
                     QDAPClient("127.0.0.1", port) as c2:

            await c1.send_multiframe([b"from_client_1"])
            await c2.send_multiframe([b"from_client_2"])

            await asyncio.sleep(0.2)

            received = srv.drain()
            assert len(received) == 2

            payloads = {r.subframes[0].payload for r in received}
            assert b"from_client_1" in payloads
            assert b"from_client_2" in payloads

        await srv.stop()

    @pytest.mark.asyncio
    async def test_five_clients_concurrent(self):
        """Five clients sending concurrently."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)
        await srv.start()

        clients = []
        for i in range(5):
            c = QDAPClient("127.0.0.1", port)
            await c.connect()
            clients.append(c)

        # All send at once
        tasks = [
            c.send_multiframe([f"client_{i}".encode()])
            for i, c in enumerate(clients)
        ]
        await asyncio.gather(*tasks)

        await asyncio.sleep(0.3)

        received = srv.drain()
        assert len(received) == 5

        for c in clients:
            await c.close()

        await srv.stop()


class TestCallbackSystem:
    """Test frame callback system."""

    @pytest.mark.asyncio
    async def test_on_frame_callback(self):
        """Callback should fire for each received frame."""
        port = _find_free_port()
        srv = QDAPServer("127.0.0.1", port)

        received_in_callback = []

        def callback(frame: QFrame, addr: tuple):
            received_in_callback.append((frame, addr))

        srv.on_frame(callback)
        await srv.start()

        client = QDAPClient("127.0.0.1", port)
        await client.connect()

        await client.send_multiframe([b"callback test"])
        await asyncio.sleep(0.1)

        assert len(received_in_callback) == 1
        frame, addr = received_in_callback[0]
        assert frame.subframes[0].payload == b"callback test"

        await client.close()
        await srv.stop()
