# tests/test_ws_adapter.py

from qdap.websocket.priority_rules import message_to_priority


class TestPriorityRules:

    def test_emergency_json(self):
        p, d = message_to_priority('{"type": "emergency", "msg": "FIRE"}')
        assert p == 1000
        assert d <= 10.0

    def test_sensor_json(self):
        p, d = message_to_priority('{"type": "sensor", "value": 42}')
        assert p == 500

    def test_binary_default(self):
        p, d = message_to_priority(b"\x00\x01\x02")
        assert p == 200

    def test_unknown_type_default(self):
        p, d = message_to_priority('{"type": "unknown_xyz"}')
        assert p == 200

    def test_log_low_priority(self):
        p, d = message_to_priority('{"type": "log", "msg": "info"}')
        assert p <= 100

    def test_audio_high_priority(self):
        p, d = message_to_priority('{"type": "audio", "chunk": "..."}')
        assert p >= 800
        assert d <= 100.0
