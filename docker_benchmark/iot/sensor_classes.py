"""
IoT Sensor Classes
======================
"""

from dataclasses import dataclass
from enum import Enum


class SensorClass(Enum):
    EMERGENCY = "emergency"
    ROUTINE = "routine"
    TELEMETRY = "telemetry"


@dataclass
class SensorMessage:
    sensor_id: int
    sensor_class: SensorClass
    payload: bytes
    deadline_ms: float
    sent_at_ns: int = 0

    @classmethod
    def emergency(cls, sensor_id: int) -> 'SensorMessage':
        return cls(
            sensor_id=sensor_id,
            sensor_class=SensorClass.EMERGENCY,
            payload=b"FIRE_ALERT" + b"\x00" * 54,
            deadline_ms=2.0,
        )

    @classmethod
    def routine(cls, sensor_id: int) -> 'SensorMessage':
        return cls(
            sensor_id=sensor_id,
            sensor_class=SensorClass.ROUTINE,
            payload=b"TEMP_HUM" + b"\x00" * 992,
            deadline_ms=500.0,
        )

    @classmethod
    def telemetry(cls, sensor_id: int) -> 'SensorMessage':
        return cls(
            sensor_id=sensor_id,
            sensor_class=SensorClass.TELEMETRY,
            payload=b"BATT_SIG" + b"\x00" * 56,
            deadline_ms=5000.0,
        )
