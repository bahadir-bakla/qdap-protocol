"""
Throughput Benchmark
=====================

Measures bulk data transfer rate through the QDAP TCP adapter.
Sends N MB of data in 64KB QFrame chunks and measures MB/s.
"""

from __future__ import annotations

import asyncio
import time

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.frame.qframe import QFrame, Subframe, SubframeType


async def measure_throughput(
    host: str,
    port: int,
    payload_size_mb: int = 10,
    chunk_size: int = 64 * 1024,
) -> dict:
    """
    N MB veriyi QDAP üzerinden gönder, throughput'u ölç.

    Senaryo: Tek büyük transfer (bulk mode testi)
    QFT Scheduler bu senaryoda BulkTransferStrategy seçmeli.
    """
    total_bytes = payload_size_mb * 1024 * 1024
    chunk = b'X' * chunk_size
    sent_bytes = 0

    adapter = QDAPTCPAdapter()
    await adapter.connect(host, port)

    t0 = time.monotonic()

    while sent_bytes < total_bytes:
        remaining = total_bytes - sent_bytes
        data = chunk[:min(chunk_size, remaining)]
        frame = QFrame.create([
            Subframe(payload=data, type=SubframeType.DATA, deadline_ms=1000)
        ])
        await adapter.send_frame(frame)
        sent_bytes += len(data)

    elapsed = time.monotonic() - t0
    stats = adapter.get_transport_stats()

    await adapter.close()

    return {
        "payload_mb": payload_size_mb,
        "elapsed_sec": elapsed,
        "throughput_mbps": stats["throughput_mbps"],
        "frames_sent": stats["frames_sent"],
    }
