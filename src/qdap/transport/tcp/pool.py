"""
QDAP Connection Pool — Reusable TCP Connection Management
============================================================

Maintains a pool of pre-established TCP connections to avoid
the overhead of per-request connection setup.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.transport.tcp.tuning import TCPTuningConfig


class QDAPConnectionPool:
    """
    TCP bağlantı havuzu — her request için yeni bağlantı açmak pahalı.

    min_size: Her zaman hazır bekleyen bağlantı sayısı
    max_size: Maksimum eş zamanlı bağlantı
    """

    def __init__(
        self,
        host: str,
        port: int,
        min_size: int = 2,
        max_size: int = 10,
        tuning: Optional[TCPTuningConfig] = None,
    ):
        self.host = host
        self.port = port
        self.min_size = min_size
        self.max_size = max_size
        self.tuning = tuning or TCPTuningConfig()

        self._pool: deque[QDAPTCPAdapter] = deque()
        self._active: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._not_empty: asyncio.Condition = asyncio.Condition(self._lock)

    async def initialize(self) -> None:
        """min_size kadar bağlantıyı önceden aç."""
        for _ in range(self.min_size):
            conn = await self._create_connection()
            self._pool.append(conn)

    async def acquire(self) -> QDAPTCPAdapter:
        """Havuzdan bağlantı al. Boşsa yeni aç, doluysa bekle."""
        async with self._not_empty:
            while not self._pool and self._active >= self.max_size:
                await self._not_empty.wait()

            if self._pool:
                conn = self._pool.popleft()
                if not conn.is_healthy():
                    conn = await self._create_connection()
            else:
                conn = await self._create_connection()

            self._active += 1
            return conn

    async def release(self, conn: QDAPTCPAdapter) -> None:
        """Bağlantıyı havuza geri ver."""
        async with self._not_empty:
            if conn.is_healthy() and len(self._pool) < self.min_size:
                self._pool.append(conn)
            else:
                await conn.close()
            self._active -= 1
            self._not_empty.notify()

    async def _create_connection(self) -> QDAPTCPAdapter:
        """Create a new tuned TCP connection."""
        adapter = QDAPTCPAdapter(tuning=self.tuning)
        await adapter.connect(self.host, self.port)
        return adapter

    async def close_all(self) -> None:
        """Close all connections in the pool."""
        while self._pool:
            conn = self._pool.popleft()
            await conn.close()

    @property
    def pool_size(self) -> int:
        """Number of idle connections in the pool."""
        return len(self._pool)

    @property
    def active_count(self) -> int:
        """Number of currently checked-out connections."""
        return self._active
