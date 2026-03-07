"""
Classical Request/Response Client
====================================

Every message gets an explicit 8-byte ACK from server.
This is what QDAP eliminates via Ghost Session.

v2: Added per-message timeout for large payloads.
"""

import asyncio
import time
import struct
from dataclasses import dataclass


ACK_SIZE = 8


@dataclass
class ClassicalMetrics:
    protocol: str = "Classical_ReqResp"
    n_messages: int = 0
    payload_bytes: int = 0
    ack_bytes_recv: int = 0
    total_wire_bytes: int = 0
    overhead_pct: float = 0.0
    throughput_mbps: float = 0.0
    mean_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    duration_sec: float = 0.0


async def run_classical_benchmark(
    host: str = "172.20.0.10",
    port: int = 19600,
    n_messages: int = 1000,
    payload_size: int = 1024,
) -> ClassicalMetrics:
    """
    Classical request/response with per-message timeout.
    Timeout scales with payload size: base 30s + 1s per MB.
    """
    reader, writer = await asyncio.open_connection(host, port)

    # Scale timeout with payload size
    timeout = 30.0 + (payload_size / (1024 * 1024)) * 5.0

    latencies = []
    total_sent = 0
    total_recv = 0
    ack_bytes = 0
    payload = b"D" * payload_size

    t_start = time.monotonic()

    for msg_id in range(n_messages):
        msg_body = struct.pack(">I", msg_id) + payload
        header = struct.pack(">I", len(msg_body))
        message = header + msg_body

        t0 = time.monotonic_ns()

        writer.write(message)
        await writer.drain()
        total_sent += len(message)

        # Wait for ACK with timeout
        try:
            ack = await asyncio.wait_for(reader.readexactly(ACK_SIZE), timeout=timeout)
            total_recv += len(ack)
            ack_bytes += len(ack)
        except asyncio.TimeoutError:
            print(f"  ⚠️  ACK timeout at msg {msg_id}/{n_messages} (timeout={timeout:.0f}s)")
            break

        latencies.append((time.monotonic_ns() - t0) / 1e6)

    duration = time.monotonic() - t_start
    writer.close()

    pure_payload = n_messages * payload_size
    overhead_pct = ack_bytes / max(pure_payload, 1) * 100
    throughput = (pure_payload * 8) / (duration * 1e6) if duration > 0 else 0

    import numpy as np
    arr = np.array(latencies) if latencies else np.array([0.0])

    return ClassicalMetrics(
        n_messages=len(latencies),
        payload_bytes=pure_payload,
        ack_bytes_recv=ack_bytes,
        total_wire_bytes=total_sent + total_recv,
        overhead_pct=round(overhead_pct, 4),
        throughput_mbps=round(throughput, 3),
        mean_latency_ms=round(float(np.mean(arr)), 3),
        p99_latency_ms=round(float(np.percentile(arr, 99)), 3),
        duration_sec=round(duration, 3),
    )
