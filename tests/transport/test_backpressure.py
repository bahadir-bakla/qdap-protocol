"""
Backpressure Controller Tests
================================

Tests for flow control via asyncio semaphore.
"""

from __future__ import annotations

import asyncio
import pytest

from qdap.transport.tcp.backpressure import BackpressureController


class TestBackpressureController:
    """BackpressureController tests."""

    def test_initial_state(self):
        bp = BackpressureController(high_watermark=10)
        assert bp.pressure_ratio == 0.0
        assert bp.current_load == 0
        assert not bp.is_overloaded()

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        bp = BackpressureController(high_watermark=5)

        await bp.acquire()
        assert bp.current_load == 1
        assert bp.pressure_ratio == 0.2

        bp.release()
        assert bp.current_load == 0
        assert bp.pressure_ratio == 0.0

    @pytest.mark.asyncio
    async def test_overload_detection(self):
        bp = BackpressureController(high_watermark=10)

        # Fill to 90%
        for _ in range(9):
            await bp.acquire()

        assert bp.pressure_ratio == 0.9
        assert bp.is_overloaded()

    @pytest.mark.asyncio
    async def test_blocking_at_watermark(self):
        """acquire() should block when at high_watermark."""
        bp = BackpressureController(high_watermark=3)

        # Fill up
        await bp.acquire()
        await bp.acquire()
        await bp.acquire()

        # Next acquire should block
        blocked = True

        async def try_acquire():
            nonlocal blocked
            await bp.acquire()
            blocked = False

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)  # Wait a bit
        assert blocked  # Still blocked

        bp.release()  # Free up one slot
        await asyncio.sleep(0.05)
        assert not blocked  # Now unblocked

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release(self):
        """Multiple concurrent producers/consumers."""
        bp = BackpressureController(high_watermark=5)

        async def producer():
            for _ in range(10):
                await bp.acquire()
                await asyncio.sleep(0.01)
                bp.release()

        tasks = [asyncio.create_task(producer()) for _ in range(3)]
        await asyncio.gather(*tasks)

        assert bp.current_load == 0
