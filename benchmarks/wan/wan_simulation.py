"""
WAN Simulation Benchmark
============================

Tests QDAP Ghost Session under simulated network conditions
(delay + loss) without requiring root/admin privileges.

Uses application-level delay injection, not kernel tc/pf.

Profiles:
- home_wifi:  20ms delay, 0% loss
- 4g_mobile:  50ms delay, 1% loss
- congested:  100ms delay, 5% loss

Usage:
    python benchmarks/wan/wan_simulation.py
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.session.ghost_session import GhostSession


@dataclass
class WANProfile:
    name: str
    delay_ms: float
    jitter_ms: float
    loss_rate: float


@dataclass
class WANResult:
    profile: str
    delay_ms: float
    loss_rate: float
    total_sent: int
    total_received: int
    total_lost: int
    empirical_loss_rate: float
    ghost_detected: int
    ghost_precision: float
    ghost_recall: float
    ghost_f1: float
    mean_latency_ms: float
    p99_latency_ms: float


PROFILES = [
    WANProfile("home_wifi", 20.0, 5.0, 0.00),
    WANProfile("4g_mobile", 50.0, 15.0, 0.01),
    WANProfile("congested", 100.0, 30.0, 0.05),
]


async def simulate_wan_profile(
    profile: WANProfile,
    msg_count: int = 500,
    payload_size: int = 256,
) -> WANResult:
    """
    Simulate sending packets through a lossy, delayed channel.
    Ghost Session tracks losses via ghost_window.
    Uses scaled-down delays for fast execution (1/10 of real).
    """
    alice = GhostSession(b"wan-benchmark-sess", b"wan-secret-key-0123456789abcdef")
    bob = GhostSession(b"wan-benchmark-sess", b"wan-secret-key-0123456789abcdef")

    rng = random.Random(42)
    latencies: list[float] = []
    true_losses: list[bool] = []

    for seq in range(msg_count):
        # Simulate delay (scaled down 100× for fast execution)
        delay = (profile.delay_ms + rng.gauss(0, profile.jitter_ms)) / 100
        delay = max(0.01, delay) / 1000.0  # Convert to seconds

        # Simulate loss
        lost = rng.random() < profile.loss_rate

        payload = bytes([seq % 256] * payload_size)

        t0 = time.monotonic_ns()
        frame = alice.send(payload, seq_num=seq)

        if not lost:
            # Simulate network delay
            await asyncio.sleep(delay)
            bob.on_receive(frame)
            alice.implicit_ack(seq)

        lat_ms = (time.monotonic_ns() - t0) / 1e6
        latencies.append(lat_ms)
        true_losses.append(lost)

    # Ghost Session detection: unacked packets in ghost_window
    detected_set = set(alice.ghost_window.keys())
    true_lost_set = {i for i, lost in enumerate(true_losses) if lost}

    # Precision / Recall / F1
    tp = len(detected_set & true_lost_set)
    fp = len(detected_set - true_lost_set)
    fn = len(true_lost_set - detected_set)

    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)

    total_lost = sum(true_losses)
    arr = np.array(latencies)

    return WANResult(
        profile=profile.name,
        delay_ms=profile.delay_ms,
        loss_rate=profile.loss_rate,
        total_sent=msg_count,
        total_received=msg_count - total_lost,
        total_lost=total_lost,
        empirical_loss_rate=round(total_lost / msg_count, 4),
        ghost_detected=len(detected_set),
        ghost_precision=round(precision, 4),
        ghost_recall=round(recall, 4),
        ghost_f1=round(f1, 4),
        mean_latency_ms=round(float(np.mean(arr)), 3),
        p99_latency_ms=round(float(np.percentile(arr, 99)), 3),
    )


async def run_wan_simulation():
    """Run all WAN profiles and save results."""
    print("🌐 WAN Simulation Benchmark")
    print("=" * 60)

    all_results = []

    for profile in PROFILES:
        print(f"\n📡 Profile: {profile.name} "
              f"(delay={profile.delay_ms}ms, loss={profile.loss_rate:.0%})")
        result = await simulate_wan_profile(profile)

        print(f"  Sent: {result.total_sent}, Lost: {result.total_lost} "
              f"({result.empirical_loss_rate:.1%})")
        print(f"  Ghost detected: {result.ghost_detected}")
        print(f"  Precision: {result.ghost_precision:.2%}, "
              f"Recall: {result.ghost_recall:.2%}, "
              f"F1: {result.ghost_f1:.2%}")
        print(f"  Mean latency: {result.mean_latency_ms:.1f}ms, "
              f"p99: {result.p99_latency_ms:.1f}ms")

        all_results.append(asdict(result))

    # Save
    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "wan_simulation.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n📄 Saved: {output_path}")
    print("=" * 60)
    return all_results


if __name__ == "__main__":
    asyncio.run(run_wan_simulation())
