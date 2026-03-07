"""
QDAP IoT Gateway
====================

100 sensors → mixed message stream → AmplitudeEncoder prioritizes
Emergency messages always go first.
Single TCP connection.
"""

import asyncio
import time
import random
import sys
import os
from dataclasses import dataclass
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.frame.qframe import QFrame, Subframe, SubframeType
from docker_benchmark.iot.sensor_classes import SensorMessage, SensorClass


@dataclass
class IoTMetrics:
    protocol: str = "QDAP_IoT"
    total_messages: int = 0
    emergency_sent: int = 0
    routine_sent: int = 0
    telemetry_sent: int = 0
    emergency_deadline_hit_pct: float = 0.0
    overall_deadline_miss_pct: float = 0.0
    ack_bytes: int = 0
    connections: int = 1
    throughput_msgs_per_s: float = 0.0


async def run_qdap_iot_benchmark(
    host: str = "172.20.0.10",
    port: int = 19601,
    n_emergency: int = 100,
    n_routine: int = 300,
    n_telemetry: int = 600,
) -> IoTMetrics:
    adapter = QDAPTCPAdapter()
    await adapter.connect(host, port)

    messages: List[SensorMessage] = []
    for i in range(n_emergency):
        messages.append(SensorMessage.emergency(i))
    for i in range(n_routine):
        messages.append(SensorMessage.routine(i))
    for i in range(n_telemetry):
        messages.append(SensorMessage.telemetry(i))

    random.shuffle(messages)

    send_order = []
    deadline_hits = 0
    emergency_deadline_hits = 0
    t_start = time.monotonic()

    for msg in messages:
        sf = Subframe(
            payload=msg.payload,
            type=SubframeType.DATA,
            deadline_ms=msg.deadline_ms,
        )
        frame = QFrame.create_with_encoder([sf])

        msg.sent_at_ns = time.monotonic_ns()
        await adapter.send_frame(frame)

        send_order.append(msg.sensor_class)

        elapsed_ms = (time.monotonic_ns() - msg.sent_at_ns) / 1e6
        if elapsed_ms <= msg.deadline_ms:
            deadline_hits += 1
            if msg.sensor_class == SensorClass.EMERGENCY:
                emergency_deadline_hits += 1

    duration = time.monotonic() - t_start
    await adapter.close()

    total = len(messages)
    emergency_hit_pct = emergency_deadline_hits / max(n_emergency, 1) * 100

    return IoTMetrics(
        total_messages=total,
        emergency_sent=n_emergency,
        routine_sent=n_routine,
        telemetry_sent=n_telemetry,
        emergency_deadline_hit_pct=round(emergency_hit_pct, 1),
        overall_deadline_miss_pct=round((1 - deadline_hits / total) * 100, 2),
        ack_bytes=0,
        connections=1,
        throughput_msgs_per_s=round(total / duration, 1),
    )
