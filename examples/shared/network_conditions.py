"""
Network Condition Simulator
==============================

Simulates realistic network conditions for benchmarks.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass


@dataclass
class NetworkProfile:
    name: str
    latency_ms: float
    jitter_ms: float
    loss_rate: float
    bandwidth_mbps: float


NETWORK_PROFILES = {
    "ideal": NetworkProfile("ideal", 0, 0, 0.000, 0),
    "home_wifi": NetworkProfile("home_wifi", 5, 2, 0.001, 50),
    "4g_mobile": NetworkProfile("4g_mobile", 30, 15, 0.005, 20),
    "3g_mobile": NetworkProfile("3g_mobile", 100, 50, 0.020, 5),
    "congested": NetworkProfile("congested", 200, 100, 0.050, 2),
    "lossy": NetworkProfile("lossy", 20, 10, 0.100, 10),
}


class NetworkConditionSimulator:
    """Applies simulated network conditions (delay, loss, bandwidth)."""

    def __init__(self, profile: NetworkProfile):
        self.profile = profile
        self._rng = random.Random(42)

    async def simulate_send(self, payload_size_bytes: int) -> bool:
        """Simulate send: returns True if delivered, False if lost."""
        if self._rng.random() < self.profile.loss_rate:
            return False

        delay = self.profile.latency_ms + self._rng.gauss(0, self.profile.jitter_ms)
        delay = max(0, delay) / 1000.0

        if self.profile.bandwidth_mbps > 0:
            transmission_delay = (payload_size_bytes * 8) / (self.profile.bandwidth_mbps * 1e6)
            delay += transmission_delay

        await asyncio.sleep(delay)
        return True
