"""
QDAP over TCP Adapter
=====================

QDAP frame'lerini TCP üzerinden taşır.
Length-prefixed framing kullanır.

Wire format:
    [MAGIC(4B)] [VERSION(2B)] [LENGTH(4B)] [QFrame data(NB)]
"""

from __future__ import annotations

import socket
import struct

from qdap.frame.qframe import QFrame


class ProtocolError(Exception):
    """QDAP protocol-level error."""
    pass


class QDAPOverTCP:
    """
    QDAP frame'lerini TCP üzerinden taşır.
    Length-prefixed framing kullanır.

    Usage:
        adapter = QDAPOverTCP()
        adapter.send_frame(sock, frame)
        received = adapter.recv_frame(sock)
    """

    MAGIC = b"\x51\x44\x41\x50"  # "QDAP"
    VERSION = 1
    HEADER_FORMAT = ">4sHI"  # magic(4) + version(2) + length(4)
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 10 bytes

    def send_frame(self, sock: socket.socket, frame: QFrame) -> int:
        """
        Serialize and send a QFrame over TCP.

        Returns:
            Number of bytes sent (including transport header).
        """
        data = frame.serialize()
        header = struct.pack(self.HEADER_FORMAT, self.MAGIC, self.VERSION, len(data))
        payload = header + data
        sock.sendall(payload)
        return len(payload)

    def recv_frame(self, sock: socket.socket) -> QFrame:
        """
        Receive and deserialize a QFrame from TCP.

        Raises:
            ProtocolError: If magic bytes or version don't match.
        """
        header = self._recv_exactly(sock, self.HEADER_SIZE)
        magic, version, length = struct.unpack(self.HEADER_FORMAT, header)

        if magic != self.MAGIC:
            raise ProtocolError(f"Invalid QDAP magic: {magic!r}")
        if version != self.VERSION:
            raise ProtocolError(f"Unsupported QDAP version: {version}")

        data = self._recv_exactly(sock, length)
        return QFrame.deserialize(data)

    @staticmethod
    def _recv_exactly(sock: socket.socket, n: int) -> bytes:
        """Receive exactly n bytes from socket."""
        buffer = bytearray()
        while len(buffer) < n:
            chunk = sock.recv(n - len(buffer))
            if not chunk:
                raise ConnectionError(
                    f"Connection closed while expecting {n} bytes "
                    f"(received {len(buffer)})"
                )
            buffer.extend(chunk)
        return bytes(buffer)
