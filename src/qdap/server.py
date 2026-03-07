"""
QDAP Server — Asyncio TCP Server
==================================

Accepts QDAP connections, parses incoming QFrames,
manages Ghost Sessions per client, and delivers payloads
in amplitude-priority order to the application layer.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

from qdap.frame.qframe import QFrame, QDAP_MAGIC, QDAP_VERSION, INTEGRITY_HASH_SIZE
from qdap.frame.encoder import AmplitudeEncoder
from qdap.session.ghost_session import GhostSession
from qdap.transport.tcp_adapter import QDAPOverTCP

logger = logging.getLogger("qdap.server")

# Transport header: [MAGIC(4) | VERSION(2) | LENGTH(4)]
TRANSPORT_HEADER_FORMAT = ">4sHI"
TRANSPORT_HEADER_SIZE = struct.calcsize(TRANSPORT_HEADER_FORMAT)


@dataclass
class ClientConnection:
    """Represents a connected QDAP client."""

    address: tuple[str, int]
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    ghost_session: Optional[GhostSession] = None
    received_frames: list[QFrame] = field(default_factory=list)


class QDAPServer:
    """
    Asyncio-based QDAP TCP server.

    Accepts connections, parses QFrames with QDAP transport framing,
    and delivers payloads in amplitude-priority order.

    Usage:
        server = QDAPServer("localhost", 9000)
        await server.start()

        # In a handler:
        frames = server.drain()  # Get all received frames

        await server.stop()
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9000,
        shared_secret: bytes = b"qdap-default-secret",
    ):
        self.host = host
        self.port = port
        self.shared_secret = shared_secret
        self.clients: dict[tuple[str, int], ClientConnection] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._received_frames: list[QFrame] = []
        self._on_frame_callback: Optional[Callable[[QFrame, tuple], None]] = None

    async def start(self) -> None:
        """Start the QDAP server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        addr = self._server.sockets[0].getsockname()
        logger.info(f"QDAP Server listening on {addr[0]}:{addr[1]}")

    async def stop(self) -> None:
        """Stop the QDAP server and close all connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        for client in self.clients.values():
            client.writer.close()

        self.clients.clear()
        logger.info("QDAP Server stopped")

    def drain(self) -> list[QFrame]:
        """
        Drain all received frames, sorted by amplitude priority.

        Returns frames in order: highest amplitude subframes first.
        """
        frames = self._received_frames.copy()
        self._received_frames.clear()
        return frames

    def drain_payloads(self) -> list[bytes]:
        """
        Drain received frames and return payloads in priority order.

        Highest amplitude → first in list.
        """
        frames = self.drain()
        payloads = []
        for frame in frames:
            order = frame.send_order
            for idx in order:
                if idx < len(frame.subframes):
                    payloads.append(frame.subframes[idx].payload)
        return payloads

    def on_frame(self, callback: Callable[[QFrame, tuple], None]) -> None:
        """Register a callback for incoming frames."""
        self._on_frame_callback = callback

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected: {addr}")

        session_id = f"session-{addr[0]}-{addr[1]}".encode()
        ghost = GhostSession(session_id, self.shared_secret)

        client = ClientConnection(
            address=addr,
            reader=reader,
            writer=writer,
            ghost_session=ghost,
        )
        self.clients[addr] = client

        try:
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break

                # Ghost Session: process received frame
                if ghost:
                    verified = ghost.on_receive(frame)
                    for seq in verified:
                        ghost.implicit_ack(seq)

                client.received_frames.append(frame)
                self._received_frames.append(frame)

                if self._on_frame_callback:
                    self._on_frame_callback(frame, addr)

        except (ConnectionError, asyncio.IncompleteReadError):
            logger.info(f"Client disconnected: {addr}")
        finally:
            writer.close()
            self.clients.pop(addr, None)

    async def _read_frame(self, reader: asyncio.StreamReader) -> Optional[QFrame]:
        """Read a single QFrame from the transport stream."""
        try:
            # Read transport header
            header_data = await reader.readexactly(TRANSPORT_HEADER_SIZE)
            magic, version, length = struct.unpack(TRANSPORT_HEADER_FORMAT, header_data)

            if magic != QDAP_MAGIC:
                logger.warning(f"Invalid magic bytes: {magic!r}")
                return None

            if version != QDAP_VERSION:
                logger.warning(f"Unsupported version: {version}")
                return None

            # Read QFrame payload
            frame_data = await reader.readexactly(length)
            return QFrame.deserialize(frame_data)

        except asyncio.IncompleteReadError:
            return None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def address(self) -> tuple[str, int]:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()
        return (self.host, self.port)


class QDAPClient:
    """
    Asyncio-based QDAP TCP client.

    Connects to a QDAP server, sends QFrames with amplitude encoding,
    and manages a Ghost Session for implicit acknowledgment.

    Usage:
        async with QDAPClient("localhost", 9000) as client:
            await client.send_multiframe(
                [data_a, data_b, data_c],
                priorities=[0.8, 0.5, 0.3],
            )
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9000,
        shared_secret: bytes = b"qdap-default-secret",
    ):
        self.host = host
        self.port = port
        self.shared_secret = shared_secret
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._ghost: Optional[GhostSession] = None
        self._seq_counter = 0
        self._encoder = AmplitudeEncoder()

    async def connect(self) -> None:
        """Establish connection to QDAP server."""
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        session_id = f"client-{self.host}-{self.port}".encode()
        self._ghost = GhostSession(session_id, self.shared_secret)
        logger.info(f"Connected to QDAP server at {self.host}:{self.port}")

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._writer = None
        self._reader = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def send_frame(self, frame: QFrame) -> None:
        """Send a single QFrame over the connection."""
        if not self._writer:
            raise ConnectionError("Not connected to server")

        data = frame.serialize()
        header = struct.pack(TRANSPORT_HEADER_FORMAT, QDAP_MAGIC, QDAP_VERSION, len(data))
        self._writer.write(header + data)
        await self._writer.drain()

    async def send_multiframe(
        self,
        payloads: list[bytes],
        priorities: Optional[list[float]] = None,
        deadline_ms: Optional[list[float]] = None,
    ) -> QFrame:
        """
        Send multiple payloads in a single QFrame with amplitude encoding.

        Args:
            payloads: List of payload bytes to send.
            priorities: Optional explicit priority weights. If None,
                        AmplitudeEncoder computes from metadata.
            deadline_ms: Optional per-payload deadline in ms.

        Returns:
            The sent QFrame.
        """
        from qdap.frame.qframe import Subframe, SubframeType
        import numpy as np

        subframes = []
        for i, payload in enumerate(payloads):
            dl = deadline_ms[i] if deadline_ms and i < len(deadline_ms) else 1000.0
            seq = self._next_seq()
            subframes.append(Subframe(
                payload=payload,
                type=SubframeType.DATA,
                deadline_ms=dl,
                seq_num=seq,
            ))

        if priorities is not None:
            # Use explicit priorities, normalize
            amp = np.array(priorities, dtype=np.float32)
            norm = np.linalg.norm(amp)
            if norm > 1e-9:
                amp = amp / norm
            frame = QFrame.create(subframes=subframes, amplitude_vector=amp)
        else:
            # Auto-compute from metadata
            frame = QFrame.create_with_encoder(subframes=subframes)

        await self.send_frame(frame)
        return frame

    def _next_seq(self) -> int:
        """Get next sequence number."""
        seq = self._seq_counter
        self._seq_counter += 1
        return seq

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()
