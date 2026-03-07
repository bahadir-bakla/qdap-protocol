"""
Latency Benchmark
==================

Measures send latency distribution for small messages.
Sends N small messages and computes p50/p95/p99/p999 percentiles.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.frame.qframe import QFrame, Subframe, SubframeType


async def measure_latency(
    host: str,
    port: int,
    n_msgs: int = 10_000,
    msg_size: int = 100,
) -> dict:
    """
    N küçük mesaj gönder ve send sürelerini ölç.

    Senaryo: Yüksek frekans küçük mesajlar (IoT / RPC tarzı)
    QFT Scheduler bu senaryoda LatencyFirstStrategy seçmeli.
    """
    payload = b'Q' * msg_size
    latencies_ns = []

    adapter = QDAPTCPAdapter()
    await adapter.connect(host, port)

    for i in range(n_msgs):
        frame = QFrame.create([
            Subframe(payload=payload, type=SubframeType.DATA, deadline_ms=5)
        ])

        t0 = time.monotonic_ns()
        await adapter.send_frame(frame)
        elapsed = time.monotonic_ns() - t0

        latencies_ns.append(elapsed)

        # %1 ihtimalle yield — gerçekçi trafik simülasyonu
        if i % 100 == 0:
            await asyncio.sleep(0)

    await adapter.close()

    arr = np.array(latencies_ns) / 1e6  # → ms

    return {
        "n_msgs": n_msgs,
        "p50_ms": float(np.percentile(arr, 50)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "p999_ms": float(np.percentile(arr, 99.9)),
        "max_ms": float(arr.max()),
        "mean_ms": float(arr.mean()),
        "std_ms": float(arr.std()),
    }
