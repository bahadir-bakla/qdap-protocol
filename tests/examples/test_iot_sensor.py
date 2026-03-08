"""
IoT Sensor Tests
==================
"""

import asyncio
import pytest
import random

from examples.iot.sensor import SensorSimulator, SensorType, SensorReading


class TestSensorSimulator:

    def test_reading_serialization_roundtrip(self):
        sim = SensorSimulator(1, SensorType.EMERGENCY)
        reading = sim._make_reading()
        data = reading.serialize()
        assert len(data) == 20
        restored = SensorReading.deserialize(data)
        assert restored.sensor_id == reading.sensor_id
        assert restored.sensor_type == reading.sensor_type

    def test_alert_has_lower_deadline(self):
        sim = SensorSimulator(1, SensorType.EMERGENCY, alert_rate=1.0)
        reading = sim._make_reading()
        assert reading.deadline_ms == 2.0
        assert reading.is_alert

    def test_environment_sensor_produces_temp(self):
        sim = SensorSimulator(10, SensorType.ENVIRONMENT)
        reading = sim._make_reading()
        assert reading.unit == "C"
        assert reading.sensor_type == SensorType.ENVIRONMENT

    def test_telemetry_normal_deadline(self):
        random.seed(42)
        sim = SensorSimulator(0, SensorType.TELEMETRY, alert_rate=0.0)
        reading = sim._make_reading()
        assert reading.deadline_ms == 500.0
        assert reading.unit == "%"

    def test_telemetry_alert_deadline(self):
        sim = SensorSimulator(0, SensorType.TELEMETRY, alert_rate=1.0)
        reading = sim._make_reading()
        assert reading.deadline_ms == 2.0

    def test_telemetry_stochastic_distribution(self):
        random.seed(123)
        sim = SensorSimulator(0, SensorType.TELEMETRY, alert_rate=0.01)
        alerts = sum(
            1 for _ in range(10000)
            if sim._make_reading().deadline_ms == 2.0
        )
        assert 50 <= alerts <= 150

    @pytest.mark.asyncio
    async def test_generates_readings(self):
        sim = SensorSimulator(1, SensorType.ENVIRONMENT)
        queue = asyncio.Queue()
        task = asyncio.create_task(sim.generate(queue))
        await asyncio.sleep(0.15)
        sim.stop()
        task.cancel()
        assert not queue.empty()
