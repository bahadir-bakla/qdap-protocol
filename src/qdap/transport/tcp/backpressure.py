"""
Backpressure Controller — Asyncio-Based Flow Control
======================================================

Prevents memory exhaustion when producer outpaces consumer.
Uses an asyncio Semaphore to limit in-flight frames.
"""

from __future__ import annotations

import asyncio


class BackpressureController:
    """
    Gönderici hızını alıcı kapasitesiyle dengele.

    high_watermark: kaç frame'e kadar buffer'la
    Aşılırsa → send_frame() bloklanır (await ile)
    Alıcı tükettikçe → blok açılır

    Bu olmadan: producer çok hızlı giderse memory patlar.
    """

    def __init__(self, high_watermark: int = 256):
        self._semaphore = asyncio.Semaphore(high_watermark)
        self._high = high_watermark
        self._current = 0

    async def acquire(self) -> None:
        """Acquire a slot. Blocks if at high watermark."""
        await self._semaphore.acquire()
        self._current += 1

    def release(self) -> None:
        """Release a slot after frame is sent."""
        self._semaphore.release()
        self._current = max(0, self._current - 1)

    @property
    def pressure_ratio(self) -> float:
        """0.0 = boş, 1.0 = tam dolu."""
        return self._current / self._high if self._high > 0 else 0.0

    @property
    def current_load(self) -> int:
        """Current number of in-flight frames."""
        return self._current

    def is_overloaded(self) -> bool:
        """True if pressure exceeds 90%."""
        return self.pressure_ratio >= 0.9
