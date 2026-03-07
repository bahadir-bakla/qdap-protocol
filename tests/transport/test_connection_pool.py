"""
Connection Pool Tests
=======================

Tests for QDAPConnectionPool: initialization, acquire/release,
health checks, and capacity limits.
"""

from __future__ import annotations

import asyncio
import socket
import pytest

from qdap.transport.tcp.pool import QDAPConnectionPool
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.transport.tcp.tuning import TCPTuningConfig


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestConnectionPool:
    """QDAPConnectionPool tests."""

    @pytest.mark.asyncio
    async def test_initialize_creates_min_connections(self):
        port = _find_free_port()
        server = QDAPTCPAdapter()
        await server.listen("127.0.0.1", port)

        pool = QDAPConnectionPool("127.0.0.1", port, min_size=3, max_size=5)
        await pool.initialize()

        assert pool.pool_size == 3
        assert pool.active_count == 0

        await pool.close_all()
        await server.close()

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        port = _find_free_port()
        server = QDAPTCPAdapter()
        await server.listen("127.0.0.1", port)

        pool = QDAPConnectionPool("127.0.0.1", port, min_size=2, max_size=5)
        await pool.initialize()

        conn = await pool.acquire()
        assert isinstance(conn, QDAPTCPAdapter)
        assert conn.is_healthy()
        assert pool.active_count == 1

        await pool.release(conn)
        assert pool.active_count == 0

        await pool.close_all()
        await server.close()

    @pytest.mark.asyncio
    async def test_acquire_beyond_pool_creates_new(self):
        port = _find_free_port()
        server = QDAPTCPAdapter()
        await server.listen("127.0.0.1", port)

        pool = QDAPConnectionPool("127.0.0.1", port, min_size=1, max_size=5)
        await pool.initialize()

        # Acquire more than min_size
        conns = []
        for _ in range(3):
            conns.append(await pool.acquire())

        assert pool.active_count == 3

        for conn in conns:
            await pool.release(conn)

        await pool.close_all()
        await server.close()

    @pytest.mark.asyncio
    async def test_send_frame_through_pool(self):
        """Full roundtrip: acquire from pool → send frame → release."""
        port = _find_free_port()
        received = []

        server = QDAPTCPAdapter(on_frame=lambda f: received.append(f))
        await server.listen("127.0.0.1", port)

        # Use min_size=0 to avoid pre-creating connections that race
        pool = QDAPConnectionPool("127.0.0.1", port, min_size=0, max_size=5)

        from qdap.frame.qframe import QFrame, Subframe

        conn = await pool.acquire()
        frame = QFrame.create([Subframe(payload=b"pool test")])
        await conn.send_frame(frame)
        await pool.release(conn)

        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].subframes[0].payload == b"pool test"

        await pool.close_all()
        await server.close()
