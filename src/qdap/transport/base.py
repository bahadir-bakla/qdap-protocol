"""
QDAP Transport — Base Interface
=================================

Abstract interface all transport adapters must implement.
TCP, QUIC, WebSocket, in-process — all conform to this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from qdap.frame.qframe import QFrame


class QDAPTransport(ABC):
    """
    Tüm transport adapter'ların uygulaması gereken interface.
    TCP, QUIC, WebSocket, in-process — hepsi bu sözleşmeye uyar.
    """

    @abstractmethod
    async def connect(self, host: str, port: int) -> None:
        """Connect to a remote QDAP endpoint."""
        ...

    @abstractmethod
    async def listen(self, host: str, port: int) -> None:
        """Start listening for incoming connections."""
        ...

    @abstractmethod
    async def send_frame(self, frame: QFrame) -> None:
        """Send a QFrame over the transport."""
        ...

    @abstractmethod
    async def recv_frame(self) -> QFrame:
        """Receive a QFrame from the transport."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""
        ...

    @abstractmethod
    def is_healthy(self) -> bool:
        """Check if the transport is in a healthy state."""
        ...

    def get_transport_stats(self) -> dict[str, Any]:
        """Return transport-level statistics."""
        raise NotImplementedError
