"""
Transport End-to-End Tests
============================

Tests verifying the full transport pipeline including
loopback transport, adapter with encoding, and stats.
"""

from __future__ import annotations

import asyncio
import socket
import pytest
import numpy as np

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.transport.loopback import LoopbackTransport
from qdap.frame.qframe import QFrame, Subframe, SubframeType


class TestLoopbackTransport:
    """Loopback (in-process) transport tests."""

    @pytest.mark.asyncio
    async def test_create_pair(self):
        t1, t2 = LoopbackTransport.create_pair()
        assert t1.is_healthy()
        assert t2.is_healthy()

    @pytest.mark.asyncio
    async def test_send_receive(self):
        t1, t2 = LoopbackTransport.create_pair()

        frame = QFrame.create([Subframe(payload=b"loopback test")])
        await t1.send_frame(frame)
        received = await t2.recv_frame()

        assert received.subframes[0].payload == b"loopback test"

    @pytest.mark.asyncio
    async def test_bidirectional(self):
        t1, t2 = LoopbackTransport.create_pair()

        # t1 → t2
        f1 = QFrame.create([Subframe(payload=b"ping")])
        await t1.send_frame(f1)
        r1 = await t2.recv_frame()
        assert r1.subframes[0].payload == b"ping"

        # t2 → t1
        f2 = QFrame.create([Subframe(payload=b"pong")])
        await t2.send_frame(f2)
        r2 = await t1.recv_frame()
        assert r2.subframes[0].payload == b"pong"

    @pytest.mark.asyncio
    async def test_stats(self):
        t1, t2 = LoopbackTransport.create_pair()

        for i in range(5):
            await t1.send_frame(QFrame.create([Subframe(payload=b"x")]))
            await t2.recv_frame()

        stats = t1.get_transport_stats()
        assert stats["frames_sent"] == 5
        assert stats["type"] == "loopback"

    @pytest.mark.asyncio
    async def test_close(self):
        t1, t2 = LoopbackTransport.create_pair()
        await t1.close()
        assert not t1.is_healthy()


class TestTransportEndToEnd:
    """Full transport pipeline tests."""

    @pytest.mark.asyncio
    async def test_multiframe_through_adapter(self):
        """Send multiframe QFrame through TCP adapter."""
        port = self._find_free_port()
        received = []

        server = QDAPTCPAdapter(on_frame=lambda f: received.append(f))
        await server.listen("127.0.0.1", port)

        client = QDAPTCPAdapter()
        await client.connect("127.0.0.1", port)

        # Multi-subframe with encoder
        subframes = [
            Subframe(payload=b"video" * 100, deadline_ms=16, seq_num=1),
            Subframe(payload=b"audio" * 10, deadline_ms=8, seq_num=2),
            Subframe(payload=b"ctrl", deadline_ms=4, seq_num=3),
        ]
        frame = QFrame.create_with_encoder(subframes=subframes)
        await client.send_frame(frame)

        await asyncio.sleep(0.1)

        assert len(received) == 1
        r = received[0]
        assert r.subframe_count == 3

        # Amplitude normalization preserved
        sum_sq = np.sum(r.amplitude_vector.astype(np.float64) ** 2)
        assert abs(sum_sq - 1.0) < 1e-4

        # seq_nums preserved
        assert r.subframes[0].seq_num == 1
        assert r.subframes[1].seq_num == 2
        assert r.subframes[2].seq_num == 3

        stats = client.get_transport_stats()
        assert stats["frames_sent"] == 1
        assert stats["p99_latency_ms"] >= 0

        await client.close()
        await server.close()

    @pytest.mark.asyncio
    async def test_sequential_sends_stats(self):
        """Stats should accumulate over multiple sends."""
        port = self._find_free_port()
        received = []

        server = QDAPTCPAdapter(on_frame=lambda f: received.append(f))
        await server.listen("127.0.0.1", port)

        client = QDAPTCPAdapter()
        await client.connect("127.0.0.1", port)

        for i in range(50):
            frame = QFrame.create([Subframe(payload=f"msg_{i}".encode())])
            await client.send_frame(frame)

        await asyncio.sleep(0.3)

        stats = client.get_transport_stats()
        assert stats["frames_sent"] == 50
        assert stats["bytes_sent"] > 0
        assert stats["throughput_mbps"] > 0

        await client.close()
        await server.close()

    @staticmethod
    def _find_free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
