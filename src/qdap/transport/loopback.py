"""
Loopback Transport — In-Process Transport for Testing
=======================================================

No TCP overhead — uses asyncio.Queue for direct frame passing.
Ideal for unit tests that need transport semantics without sockets.
"""

from __future__ import annotations

import asyncio
from typing import Any

from qdap.frame.qframe import QFrame
from qdap.transport.base import QDAPTransport


class LoopbackTransport(QDAPTransport):
    """
    In-process transport using asyncio.Queue.

    Two LoopbackTransport instances share a queue pair for bidirectional
    communication without TCP overhead.

    Usage:
        client_transport, server_transport = LoopbackTransport.create_pair()
        await client_transport.send_frame(frame)
        received = await server_transport.recv_frame()
    """

    def __init__(
        self,
        send_queue: asyncio.Queue[QFrame],
        recv_queue: asyncio.Queue[QFrame],
    ):
        self._send_queue = send_queue
        self._recv_queue = recv_queue
        self._healthy = True
        self._frames_sent = 0
        self._frames_received = 0

    @classmethod
    def create_pair(cls) -> tuple[LoopbackTransport, LoopbackTransport]:
        """Create a bidirectional pair of loopback transports."""
        q1: asyncio.Queue[QFrame] = asyncio.Queue()
        q2: asyncio.Queue[QFrame] = asyncio.Queue()
        return cls(q1, q2), cls(q2, q1)

    async def connect(self, host: str, port: int) -> None:
        """No-op for loopback."""
        self._healthy = True

    async def listen(self, host: str, port: int) -> None:
        """No-op for loopback."""
        self._healthy = True

    async def send_frame(self, frame: QFrame) -> None:
        """Send a frame through the in-process queue."""
        if not self._healthy:
            raise ConnectionError("Loopback transport is closed")
        await self._send_queue.put(frame)
        self._frames_sent += 1

    async def recv_frame(self) -> QFrame:
        """Receive a frame from the in-process queue."""
        if not self._healthy:
            raise ConnectionError("Loopback transport is closed")
        frame = await self._recv_queue.get()
        self._frames_received += 1
        return frame

    async def close(self) -> None:
        """Close the loopback transport."""
        self._healthy = False

    def is_healthy(self) -> bool:
        return self._healthy

    def get_transport_stats(self) -> dict[str, Any]:
        return {
            "type": "loopback",
            "frames_sent": self._frames_sent,
            "frames_received": self._frames_received,
        }
