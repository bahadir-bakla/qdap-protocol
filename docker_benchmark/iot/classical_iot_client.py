"""
Classical FIFO IoT Baseline
================================

Messages sent in FIFO order (no priority).
Each sensor opens its own connection (realistic IoT pattern).
"""

import asyncio
import time
import random
import struct
import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from docker_benchmark.iot.sensor_classes import SensorMessage, SensorClass

ACK_SIZE = 8


@dataclass
class ClassicalIoTMetrics:
    protocol: str = "Classical_FIFO"
    total_messages: int = 0
    emergency_deadline_hit_pct: float = 0.0
    overall_deadline_miss_pct: float = 0.0
    connections: int = 0
    throughput_msgs_per_s: float = 0.0


async def send_one(host, port, msg: SensorMessage, results: list, semaphore):
    """Each sensor sends on its own connection with concurrency limit."""
    async with semaphore:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10.0
            )
            t0 = time.monotonic_ns()

            msg_body = struct.pack(">I", msg.sensor_id) + msg.payload
            header = struct.pack(">I", len(msg_body))
            writer.write(header + msg_body)
            await writer.drain()

            try:
                await asyncio.wait_for(reader.readexactly(ACK_SIZE), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                pass

            elapsed = (time.monotonic_ns() - t0) / 1e6
            results.append({
                "class": msg.sensor_class,
                "elapsed_ms": elapsed,
                "deadline_ms": msg.deadline_ms,
                "met": elapsed <= msg.deadline_ms,
            })
            writer.close()
        except Exception:
            results.append({
                "class": msg.sensor_class,
                "elapsed_ms": 9999,
                "deadline_ms": msg.deadline_ms,
                "met": False,
            })


async def run_classical_iot_benchmark(
    host: str = "172.20.0.10",
    port: int = 19600,
    n_emergency: int = 100,
    n_routine: int = 300,
    n_telemetry: int = 600,
) -> ClassicalIoTMetrics:
    messages = []
    for i in range(n_emergency):
        messages.append(SensorMessage.emergency(i))
    for i in range(n_routine):
        messages.append(SensorMessage.routine(i))
    for i in range(n_telemetry):
        messages.append(SensorMessage.telemetry(i))
    random.shuffle(messages)

    results = []
    semaphore = asyncio.Semaphore(50)  # max 50 concurrent connections
    t_start = time.monotonic()

    tasks = [send_one(host, port, msg, results, semaphore) for msg in messages]
    await asyncio.gather(*tasks)

    duration = time.monotonic() - t_start

    emergency_met = sum(1 for r in results
                       if r["class"] == SensorClass.EMERGENCY and r["met"])
    deadline_met = sum(1 for r in results if r["met"])
    total = len(results)

    return ClassicalIoTMetrics(
        total_messages=total,
        emergency_deadline_hit_pct=round(emergency_met / max(n_emergency, 1) * 100, 1),
        overall_deadline_miss_pct=round((1 - deadline_met / max(total, 1)) * 100, 2),
        connections=len(messages),
        throughput_msgs_per_s=round(total / duration, 1),
    )
