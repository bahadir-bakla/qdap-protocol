"""
IoT Sensor Simulator
======================

Simulates 3 types of IoT sensors with different priorities:
- EMERGENCY: fire/gas (deadline=2ms, 100ms interval)
- ENVIRONMENT: temp/humidity (deadline=50ms, 1s interval)
- TELEMETRY: battery/signal (deadline=500ms, 10s interval)
"""

from __future__ import annotations

import asyncio
import random
import struct
import time
from dataclasses import dataclass
from enum import IntEnum


class SensorType(IntEnum):
    EMERGENCY = 1
    ENVIRONMENT = 2
    TELEMETRY = 3


SENSOR_CONFIG = {
    SensorType.EMERGENCY: {
        'deadline_ms': 2, 'update_interval': 0.1,
        'payload_size': 32, 'priority_base': 1.0,
    },
    SensorType.ENVIRONMENT: {
        'deadline_ms': 50, 'update_interval': 1.0,
        'payload_size': 64, 'priority_base': 0.5,
    },
    SensorType.TELEMETRY: {
        'deadline_ms': 500, 'update_interval': 10.0,
        'payload_size': 128, 'priority_base': 0.1,
    },
}


@dataclass
class SensorReading:
    sensor_id: int
    sensor_type: SensorType
    timestamp_ns: int
    value: float
    unit: str
    is_alert: bool = False
    deadline_ms: float = 50.0

    def serialize(self) -> bytes:
        """Wire format: [sensor_id(2)][type(1)][timestamp(8)][value(4)][flags(1)][unit(4)] = 20 bytes."""
        flags = 0x01 if self.is_alert else 0x00
        unit_bytes = self.unit.encode()[:4].ljust(4, b'\x00')
        return struct.pack('>HBqfB4s', self.sensor_id, int(self.sensor_type),
                           self.timestamp_ns, self.value, flags, unit_bytes)

    @classmethod
    def deserialize(cls, data: bytes) -> SensorReading:
        sensor_id, stype, ts, value, flags, unit_b = struct.unpack('>HBqfB4s', data)
        return cls(
            sensor_id=sensor_id, sensor_type=SensorType(stype),
            timestamp_ns=ts, value=value,
            unit=unit_b.decode().rstrip('\x00'),
            is_alert=bool(flags & 0x01),
        )


class SensorSimulator:
    """Single sensor simulator — produces readings at configured intervals."""

    def __init__(self, sensor_id: int, sensor_type: SensorType, alert_rate: float = 0.01):
        self.sensor_id = sensor_id
        self.sensor_type = sensor_type
        self.alert_rate = alert_rate
        self.config = SENSOR_CONFIG[sensor_type]
        self._running = False

    async def generate(self, queue: asyncio.Queue):
        """Continuously generate readings into queue."""
        self._running = True
        interval = self.config['update_interval']
        while self._running:
            reading = self._make_reading()
            await queue.put(reading)
            await asyncio.sleep(interval)

    def _make_reading(self) -> SensorReading:
        is_alert = random.random() < self.alert_rate
        if self.sensor_type == SensorType.EMERGENCY:
            value = random.uniform(80, 100) if is_alert else random.uniform(0, 100)
            unit = "ppm"
        elif self.sensor_type == SensorType.ENVIRONMENT:
            value = random.gauss(22.0, 3.0)
            unit = "C"
        else:
            value = random.uniform(0, 100)
            unit = "%"

        deadline = 2.0 if is_alert else float(self.config['deadline_ms'])
        return SensorReading(
            sensor_id=self.sensor_id, sensor_type=self.sensor_type,
            timestamp_ns=time.monotonic_ns(), value=value, unit=unit,
            is_alert=is_alert, deadline_ms=deadline,
        )

    def stop(self):
        self._running = False
