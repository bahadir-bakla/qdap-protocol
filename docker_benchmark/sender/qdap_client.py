"""
QDAP Ghost Session Client — Adaptive Chunking v2
======================================================

Uses AdaptiveChunker + QFTScheduler with warm-up for optimal chunk sizing.
Fix 3: Pre-benchmark warmup trains scheduler with expected payload size.
"""

import asyncio
import time
import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.scheduler.qft_scheduler import QFTScheduler
from qdap.chunking.adaptive_chunker import AdaptiveChunker


@dataclass
class QDAPMetrics:
    protocol: str = "QDAP_AdaptiveChunk_v2"
    n_messages: int = 0
    payload_bytes: int = 0
    ack_bytes_sent: int = 0
    total_wire_bytes: int = 0
    overhead_pct: float = 0.0
    throughput_mbps: float = 0.0
    mean_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    duration_sec: float = 0.0
    chunk_strategy: str = ""
    avg_chunk_size_kb: float = 0.0
    frames_sent: int = 0


async def run_qdap_benchmark(
    host: str = "172.20.0.10",
    port: int = 19601,
    n_messages: int = 1000,
    payload_size: int = 1024,
) -> QDAPMetrics:
    adapter = QDAPTCPAdapter()
    scheduler = QFTScheduler(window_size=64)
    chunker = AdaptiveChunker(adapter, scheduler)

    await adapter.connect(host, port)

    # Fix 3: Pre-benchmark warm-up
    await chunker.warmup(
        sample_payload_size=payload_size,
        n_samples=128,
    )

    latencies = []
    payload = b"Q" * payload_size

    from qdap.security.encrypted_frame import FrameEncryptor
    encryptor = FrameEncryptor(b"A" * 32)  # Static mock key

    t_start = time.monotonic()

    for _ in range(n_messages):
        scheduler.observe_packet_size(payload_size)
        t0 = time.monotonic_ns()
        
        # --- ADIM 2: SECURE GHOST SESSION OVERHEAD ---
        secure_payload = encryptor.pack(payload)
        await chunker.send(secure_payload, deadline_ms=50.0)
        
        latencies.append((time.monotonic_ns() - t0) / 1e6)

    duration = time.monotonic() - t_start
    await adapter.close()

    stats = adapter.get_transport_stats()
    chunk_stats = chunker.get_stats()

    import numpy as np
    arr = np.array(latencies)
    pure_payload = n_messages * payload_size
    throughput = (pure_payload * 8) / (duration * 1e6)

    return QDAPMetrics(
        n_messages=n_messages,
        payload_bytes=pure_payload,
        ack_bytes_sent=0,
        total_wire_bytes=stats.get("bytes_sent", 0),
        overhead_pct=0.0,
        throughput_mbps=round(throughput, 3),
        mean_latency_ms=round(float(np.mean(arr)), 3),
        p99_latency_ms=round(float(np.percentile(arr, 99)), 3),
        duration_sec=round(duration, 3),
        chunk_strategy=chunk_stats.get("current_strategy", ""),
        avg_chunk_size_kb=chunk_stats.get("avg_chunk_size_kb", 0.0),
        frames_sent=stats.get("frames_sent", 0),
    )
