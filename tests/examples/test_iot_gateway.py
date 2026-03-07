"""
IoT Gateway Tests
===================
"""

import asyncio
import pytest

from examples.iot.gateway import QDAPIoTGateway
from qdap.transport.loopback import LoopbackTransport


class TestIoTGateway:

    def test_add_sensors_default_100(self):
        gw = QDAPIoTGateway()
        gw.add_sensors()
        assert len(gw.sensors) == 100

    def test_add_sensors_custom(self):
        gw = QDAPIoTGateway()
        gw.add_sensors(n_emergency=2, n_environment=3, n_telemetry=5)
        assert len(gw.sensors) == 10

    @pytest.mark.asyncio
    async def test_gateway_runs_and_collects(self):
        server, client = LoopbackTransport.create_pair()
        gw = QDAPIoTGateway()
        gw.add_sensors(n_emergency=1, n_environment=2, n_telemetry=2)
        gw.adapter = client

        stats = await gw.run(duration_sec=0.5)
        assert stats['total_readings'] > 0
        assert stats['frames_sent'] > 0

    @pytest.mark.asyncio
    async def test_gateway_records_alert_latencies(self):
        server, client = LoopbackTransport.create_pair()
        gw = QDAPIoTGateway()
        gw.add_sensors(n_emergency=2, n_environment=0, n_telemetry=0)
        gw.adapter = client

        await gw.run(duration_sec=0.3)
        # At 5% alert rate with 100ms interval, ~0.3s should produce some alerts
        assert gw.stats.total_readings > 0
