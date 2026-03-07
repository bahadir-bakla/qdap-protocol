"""
TCP Adapter Unit Tests
========================

Tests for QDAPTCPAdapter: socket tuning, framing,
send/recv, stats, and error handling.
"""

from __future__ import annotations

import asyncio
import socket
import pytest

from qdap.transport.tcp.adapter import (
    QDAPTCPAdapter,
    TCPAdapterStats,
    TRANSPORT_HEADER_SIZE,
    ProtocolError,
)
from qdap.transport.tcp.tuning import TCPTuningConfig, apply_tuning
from qdap.frame.qframe import QFrame, Subframe, SubframeType


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestTCPTuningConfig:
    """TCPTuningConfig tests."""

    def test_defaults(self):
        cfg = TCPTuningConfig()
        assert cfg.tcp_nodelay is True
        assert cfg.send_buffer_size == 4 * 1024 * 1024
        assert cfg.keepalive_enabled is True
        assert cfg.reuse_addr is True

    def test_apply_tuning_to_socket(self):
        """apply_tuning should not crash on real socket."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            apply_tuning(s, TCPTuningConfig())
            # Verify TCP_NODELAY was set
            nodelay = s.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            assert nodelay != 0
        finally:
            s.close()

    def test_custom_config(self):
        cfg = TCPTuningConfig(
            tcp_nodelay=False,
            send_buffer_size=1024,
            keepalive_enabled=False,
        )
        assert cfg.tcp_nodelay is False
        assert cfg.send_buffer_size == 1024
        assert cfg.keepalive_enabled is False


class TestTCPAdapterStats:
    """TCPAdapterStats tests."""

    def test_empty_stats(self):
        stats = TCPAdapterStats()
        assert stats.p99_send_latency_ms() == 0.0
        assert stats.p999_send_latency_ms() == 0.0
        assert stats.throughput_mbps(0) == 0.0

    def test_throughput_calculation(self):
        stats = TCPAdapterStats(bytes_sent=10 * 1024 * 1024)  # 10MB
        mbps = stats.throughput_mbps(1.0)
        assert abs(mbps - 10.0) < 0.01

    def test_latency_percentiles(self):
        stats = TCPAdapterStats()
        stats.send_latencies_ns = list(range(1, 101))  # 1-100 ns
        p99 = stats.p99_send_latency_ms()
        assert p99 > 0

    def test_to_dict(self):
        stats = TCPAdapterStats(frames_sent=42, bytes_sent=1024)
        d = stats.to_dict(elapsed_sec=1.0)
        assert d["frames_sent"] == 42
        assert "throughput_mbps" in d
        assert "p99_latency_ms" in d


class TestQDAPTCPAdapter:
    """QDAPTCPAdapter integration tests."""

    @pytest.mark.asyncio
    async def test_send_and_receive_frame(self):
        """Send a frame from client to server via adapter."""
        port = _find_free_port()
        received_frames = []

        async def on_frame(frame: QFrame):
            received_frames.append(frame)

        server = QDAPTCPAdapter(on_frame=on_frame)
        await server.listen("127.0.0.1", port)

        client = QDAPTCPAdapter()
        await client.connect("127.0.0.1", port)

        # Send frame
        frame = QFrame.create([Subframe(payload=b"adapter test", seq_num=1)])
        await client.send_frame(frame)

        await asyncio.sleep(0.1)

        assert len(received_frames) == 1
        assert received_frames[0].subframes[0].payload == b"adapter test"
        assert received_frames[0].subframes[0].seq_num == 1

        # Check stats
        client_stats = client.get_transport_stats()
        assert client_stats["frames_sent"] == 1
        assert client_stats["bytes_sent"] > 0

        await client.close()
        await server.close()

    @pytest.mark.asyncio
    async def test_multiple_frames(self):
        port = _find_free_port()
        received = []

        server = QDAPTCPAdapter(on_frame=lambda f: received.append(f))
        await server.listen("127.0.0.1", port)

        client = QDAPTCPAdapter()
        await client.connect("127.0.0.1", port)

        for i in range(20):
            frame = QFrame.create([Subframe(payload=f"msg_{i}".encode(), seq_num=i)])
            await client.send_frame(frame)

        await asyncio.sleep(0.2)

        assert len(received) == 20
        assert client.get_transport_stats()["frames_sent"] == 20

        await client.close()
        await server.close()

    @pytest.mark.asyncio
    async def test_health_status(self):
        port = _find_free_port()
        adapter = QDAPTCPAdapter()
        assert not adapter.is_healthy()

        server = QDAPTCPAdapter()
        await server.listen("127.0.0.1", port)
        assert server.is_healthy()

        await adapter.connect("127.0.0.1", port)
        assert adapter.is_healthy()

        await adapter.close()
        assert not adapter.is_healthy()

        await server.close()

    @pytest.mark.asyncio
    async def test_large_frame(self):
        """64KB payload through adapter."""
        port = _find_free_port()
        received = []

        server = QDAPTCPAdapter(on_frame=lambda f: received.append(f))
        await server.listen("127.0.0.1", port)

        client = QDAPTCPAdapter()
        await client.connect("127.0.0.1", port)

        big_payload = b"\xAA" * 65536
        frame = QFrame.create([Subframe(payload=big_payload)])
        await client.send_frame(frame)

        await asyncio.sleep(0.2)

        assert len(received) == 1
        assert received[0].subframes[0].payload == big_payload

        await client.close()
        await server.close()
