"""
QDAP IoT Gateway
==================

Aggregates 100 sensors over a single QDAP connection.
Emergency readings get highest amplitude → always sent first.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import List

import numpy as np

from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.scheduler.qft_scheduler import QFTScheduler
from examples.iot.sensor import SensorReading, SensorSimulator, SensorType


@dataclass
class GatewayStats:
    total_readings: int = 0
    alert_readings: int = 0
    frames_sent: int = 0
    alert_latencies: list = field(default_factory=list)
    routine_latencies: list = field(default_factory=list)

    def record_latency(self, reading: SensorReading):
        latency_ms = (time.monotonic_ns() - reading.timestamp_ns) / 1e6
        if reading.is_alert:
            self.alert_latencies.append(latency_ms)
            self.alert_readings += 1
        else:
            self.routine_latencies.append(latency_ms)
        self.total_readings += 1

    def summary(self) -> dict:
        al = self.alert_latencies
        rl = self.routine_latencies
        return {
            "total_readings": self.total_readings,
            "alert_readings": self.alert_readings,
            "frames_sent": self.frames_sent,
            "alert_p99_ms": float(np.percentile(al, 99)) if al else 0,
            "alert_mean_ms": float(np.mean(al)) if al else 0,
            "routine_p99_ms": float(np.percentile(rl, 99)) if rl else 0,
            "routine_mean_ms": float(np.mean(rl)) if rl else 0,
        }


class QDAPIoTGateway:
    """
    Manages 100 sensors over a single QDAP connection.

    1. Collects readings from all sensors
    2. Every 10ms creates a QFrame (100fps)
    3. AmplitudeEncoder determines priority
    4. Emergency → high amplitude → sent first
    5. QFT Scheduler analyzes traffic type → selects strategy
    """

    BATCH_INTERVAL_MS = 10

    def __init__(self, host: str = "127.0.0.1", port: int = 19100):
        self.host = host
        self.port = port
        self.adapter = None  # Set externally or via connect
        self.scheduler = QFTScheduler(window_size=64)
        self.stats = GatewayStats()
        self.reading_queue: asyncio.Queue[SensorReading] = asyncio.Queue(maxsize=10_000)
        self.sensors: List[SensorSimulator] = []
        self._running = False

    def add_sensors(self, n_emergency: int = 5, n_environment: int = 30, n_telemetry: int = 65):
        """Add 100 sensors (default distribution)."""
        sid = 0
        for _ in range(n_emergency):
            self.sensors.append(SensorSimulator(sid, SensorType.EMERGENCY, alert_rate=0.05))
            sid += 1
        for _ in range(n_environment):
            self.sensors.append(SensorSimulator(sid, SensorType.ENVIRONMENT))
            sid += 1
        for _ in range(n_telemetry):
            self.sensors.append(SensorSimulator(sid, SensorType.TELEMETRY))
            sid += 1

    async def run(self, duration_sec: float = 30.0) -> dict:
        """Run gateway: start sensors, batch and send frames."""
        self._running = True

        sensor_tasks = [asyncio.create_task(s.generate(self.reading_queue)) for s in self.sensors]
        send_task = asyncio.create_task(self._send_loop())

        await asyncio.sleep(duration_sec)
        self._running = False
        send_task.cancel()
        for s in self.sensors:
            s.stop()
        for t in sensor_tasks:
            t.cancel()

        return self.stats.summary()

    async def _send_loop(self):
        interval = self.BATCH_INTERVAL_MS / 1000.0
        while self._running:
            t0 = time.monotonic()
            readings = []
            while not self.reading_queue.empty():
                try:
                    readings.append(self.reading_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if readings:
                await self._send_batch(readings)

            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))

    async def _send_batch(self, readings: List[SensorReading]):
        subframes = [
            Subframe(
                payload=r.serialize(),
                type=SubframeType.DATA,
                deadline_ms=r.deadline_ms,
            )
            for r in readings
        ]

        for sf in subframes:
            self.scheduler.observe_packet_size(len(sf.payload))

        frame = QFrame.create_with_encoder(subframes)

        if self.adapter is not None:
            await self.adapter.send_frame(frame)

        self.stats.frames_sent += 1
        for reading in readings:
            self.stats.record_latency(reading)
