"""
QDAP TCP Adapter — Production-Grade Transport
================================================

Built on Phase 1.4 server.py foundation:
  + Socket-level tuning (TCP_NODELAY, buffer sizes, keepalive)
  + Backpressure controller (blocks sender if receiver is slow)
  + Connection health monitoring
  + Statistics collection (for benchmarks)
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

import numpy as np

from qdap.frame.qframe import QFrame, QDAP_MAGIC, QDAP_VERSION
from qdap.transport.base import QDAPTransport
from qdap.transport.tcp.tuning import TCPTuningConfig, apply_tuning
from qdap.transport.tcp.backpressure import BackpressureController

logger = logging.getLogger("qdap.transport.tcp")

# Wire format: [MAGIC(4) | VERSION(2) | LENGTH(4)]
TRANSPORT_HEADER_FORMAT = ">4sHI"
TRANSPORT_HEADER_SIZE = struct.calcsize(TRANSPORT_HEADER_FORMAT)  # 10 bytes


class ProtocolError(Exception):
    """QDAP protocol-level error."""
    pass


@dataclass
class TCPAdapterStats:
    """Transport-level statistics for monitoring and benchmarking."""

    frames_sent: int = 0
    frames_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    retransmit_count: int = 0
    connection_resets: int = 0
    send_latencies_ns: list[int] = field(default_factory=list)

    def p50_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        return float(np.percentile(self.send_latencies_ns, 50)) / 1e6

    def p95_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        return float(np.percentile(self.send_latencies_ns, 95)) / 1e6

    def p99_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        return float(np.percentile(self.send_latencies_ns, 99)) / 1e6

    def p999_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        return float(np.percentile(self.send_latencies_ns, 99.9)) / 1e6

    def throughput_mbps(self, elapsed_sec: float) -> float:
        if elapsed_sec <= 0:
            return 0.0
        return (self.bytes_sent / elapsed_sec) / (1024 * 1024)

    def to_dict(self, elapsed_sec: float = 0.0) -> dict[str, Any]:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "throughput_mbps": self.throughput_mbps(elapsed_sec),
            "p50_latency_ms": self.p50_send_latency_ms(),
            "p95_latency_ms": self.p95_send_latency_ms(),
            "p99_latency_ms": self.p99_send_latency_ms(),
            "p999_latency_ms": self.p999_send_latency_ms(),
            "connection_resets": self.connection_resets,
        }


class QDAPTCPAdapter(QDAPTransport):
    """
    Production-grade TCP transport adapter.

    Extends the basic asyncio TCP with:
    - Socket-level tuning (TCP_NODELAY, 4MB buffers, keepalive)
    - Backpressure controller (blocks sender when receiver is slow)
    - Connection health monitoring
    - Statistics collection (for benchmarks)
    """

    MAGIC = QDAP_MAGIC
    VERSION = QDAP_VERSION

    def __init__(
        self,
        tuning: Optional[TCPTuningConfig] = None,
        on_frame: Optional[Callable[[QFrame], Awaitable[None]]] = None,
        high_watermark: int = 256,
    ):
        self.tuning = tuning or TCPTuningConfig()
        self.on_frame = on_frame
        self.stats = TCPAdapterStats()
        self.bp = BackpressureController(high_watermark=high_watermark)

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._healthy = False
        self._start_time: float = 0.0

    # ── Connection management ──────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        """Connect to a remote QDAP endpoint with socket tuning."""
        self._reader, self._writer = await asyncio.open_connection(host, port)
        self._apply_socket_tuning()
        self._healthy = True
        self._start_time = time.monotonic()
        logger.debug(f"Connected to {host}:{port}")

    async def listen(self, host: str, port: int) -> None:
        """Start listening for incoming connections."""
        self._server = await asyncio.start_server(
            self._handle_client, host, port,
        )
        self._healthy = True
        self._start_time = time.monotonic()
        addr = self._server.sockets[0].getsockname()
        logger.info(f"Listening on {addr[0]}:{addr[1]}")

    async def serve_forever(self) -> None:
        """Serve until cancelled."""
        if self._server:
            async with self._server:
                await self._server.serve_forever()

    def _apply_socket_tuning(self) -> None:
        """Apply TCP tuning to the current socket."""
        if self._writer:
            sock = self._writer.get_extra_info('socket')
            if sock:
                apply_tuning(sock, self.tuning)

    # ── Sending ────────────────────────────────────────────────────

    async def send_frame(self, frame: QFrame) -> None:
        """
        QFrame'i wire format'a çevirip gönder.
        Backpressure kontrolü ile — alıcı yavaşsa bloklanır.
        """
        if not self._writer or self._writer.is_closing():
            raise ConnectionError("Not connected")

        await self.bp.acquire()

        t0 = time.monotonic_ns()
        data = frame.serialize()
        hdr = struct.pack(TRANSPORT_HEADER_FORMAT, self.MAGIC, self.VERSION, len(data))
        payload = hdr + data

        try:
            self._writer.write(payload)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            self.stats.connection_resets += 1
            self._healthy = False
            self.bp.release()
            raise

        elapsed = time.monotonic_ns() - t0
        self.stats.frames_sent += 1
        self.stats.bytes_sent += len(payload)
        self.stats.send_latencies_ns.append(elapsed)

        # Keep latency samples bounded
        if len(self.stats.send_latencies_ns) > 10000:
            self.stats.send_latencies_ns = self.stats.send_latencies_ns[-5000:]

        self.bp.release()

    # ── Receiving ──────────────────────────────────────────────────

    async def recv_frame(self) -> QFrame:
        """Wire'dan bir QFrame oku ve deserialize et."""
        if not self._reader:
            raise ConnectionError("Not connected")

        hdr = await self._recv_exactly(self._reader, TRANSPORT_HEADER_SIZE)
        magic, version, length = struct.unpack(TRANSPORT_HEADER_FORMAT, hdr)

        if magic != self.MAGIC:
            raise ProtocolError(f"Invalid QDAP magic: {magic!r}")
        if version != self.VERSION:
            raise ProtocolError(f"Unsupported version: {version}")

        data = await self._recv_exactly(self._reader, length)
        frame = QFrame.deserialize(data)

        self.stats.frames_received += 1
        self.stats.bytes_received += TRANSPORT_HEADER_SIZE + length
        return frame

    async def _recv_exactly(
        self, reader: asyncio.StreamReader, n: int
    ) -> bytes:
        """n byte tam olarak oku — partial read varsa bekle."""
        buf = bytearray()
        while len(buf) < n:
            chunk = await reader.read(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed mid-frame")
            buf.extend(chunk)
        return bytes(buf)

    # ── Server handler ─────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        self._reader = reader
        self._writer = writer
        self._apply_socket_tuning()

        try:
            while True:
                frame = await self.recv_frame()
                if self.on_frame:
                    result = self.on_frame(frame)
                    if asyncio.iscoroutine(result):
                        await result
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    # ── Utilities ──────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the transport connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        if self._server:
            self._server.close()
        self._healthy = False

    def is_healthy(self) -> bool:
        """Check if transport is healthy."""
        return self._healthy

    def get_transport_stats(self) -> dict[str, Any]:
        """Return transport-level statistics."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return self.stats.to_dict(elapsed)

    @property
    def address(self) -> Optional[tuple[str, int]]:
        """Return the server address if listening."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()
        return None
