# tests/test_broker.py
import asyncio
import pytest
import threading
import time
import socket
import struct

from src.qdap.broker.packet_parser import (
    build_connack, build_publish, parse_packet,
    encode_string, encode_remaining_length,
    MQTT_CONNECT, MQTT_PUBLISH, MQTT_SUBSCRIBE
)
from src.qdap.broker.topic_tree import TopicTree
from src.qdap.broker.session_store import SessionStore
from src.qdap.broker.qdap_transport import topic_to_priority

# --- Unit: TopicTree ---
def test_topic_exact_match():
    tree = TopicTree()
    tree.subscribe("c1", "sensor/temp", 0)
    matches = tree.match("sensor/temp")
    assert ("c1", 0) in matches

def test_topic_wildcard_plus():
    tree = TopicTree()
    tree.subscribe("c1", "sensor/+/temp", 0)
    assert ("c1", 0) in tree.match("sensor/room1/temp")
    assert ("c1", 0) in tree.match("sensor/room2/temp")
    assert ("c1", 0) not in tree.match("sensor/room1/humidity")

def test_topic_wildcard_hash():
    tree = TopicTree()
    tree.subscribe("c1", "hospital/#", 0)
    assert ("c1", 0) in tree.match("hospital/icu/vitals")
    assert ("c1", 0) in tree.match("hospital/er")

def test_topic_unsubscribe():
    tree = TopicTree()
    tree.subscribe("c1", "a/b", 0)
    tree.unsubscribe("c1", "a/b")
    assert tree.match("a/b") == []

def test_topic_multi_subscriber():
    tree = TopicTree()
    tree.subscribe("c1", "a/b", 0)
    tree.subscribe("c2", "a/b", 1)
    matches = dict(tree.match("a/b"))
    assert "c1" in matches and "c2" in matches

def test_topic_remove_client():
    tree = TopicTree()
    tree.subscribe("c1", "a/b", 0)
    tree.subscribe("c1", "c/d", 1)
    tree.remove_client("c1")
    assert tree.match("a/b") == []
    assert tree.match("c/d") == []

# --- Unit: Priority ---
def test_emergency_topic_priority():
    assert topic_to_priority("emergency/flood", 100) == 1000
    assert topic_to_priority("hospital/icu", 100) == 1000
    assert topic_to_priority("sensor/temp", 50) == 800  # small payload

def test_normal_priority_by_size():
    assert topic_to_priority("data/stream", 50) == 800
    assert topic_to_priority("data/stream", 500) == 500
    assert topic_to_priority("data/stream", 5000) == 100

# --- Unit: Packet Parser ---
def _build_connect_raw(client_id: str) -> bytes:
    proto = encode_string("MQTT") + bytes([5])  # MQTT 5.0
    flags = bytes([0x02])  # clean session
    keepalive = struct.pack("!H", 60)
    props = bytes([0])
    payload = encode_string(client_id)
    body = proto + flags + keepalive + props + payload
    return bytes([0x10]) + encode_remaining_length(len(body)) + body

def test_parse_connect():
    raw = _build_connect_raw("test_client")
    pkt = parse_packet(raw)
    assert pkt is not None
    assert pkt.ptype == MQTT_CONNECT
    assert pkt.client_id == "test_client"

def test_build_and_parse_publish():
    pub = build_publish("sensor/temp", b"22.5", qos=0)
    pkt = parse_packet(pub)
    assert pkt.ptype == MQTT_PUBLISH
    assert pkt.topic == "sensor/temp"
    assert pkt.payload == b"22.5"

def test_connack_bytes():
    ack = build_connack(session_present=False, reason_code=0)
    assert ack[0] == 0x20  # CONNACK fixed header
    assert ack[2] == 0x00  # session_present=0
    assert ack[3] == 0x00  # success

# --- Integration: Full broker with real TCP ---
@pytest.fixture
def running_broker():
    """Start broker in background thread, yield port, stop after test."""
    from src.qdap.broker.broker import QDAPBroker
    broker = QDAPBroker(host="127.0.0.1", port=0)

    loop = asyncio.new_event_loop()
    server_task = None
    port_holder = [None]

    async def _run():
        server = await asyncio.start_server(
            broker.handle_client, "127.0.0.1", 0)
        port_holder[0] = server.sockets[0].getsockname()[1]
        async with server:
            await server.serve_forever()

    def _thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    time.sleep(0.5)  # wait for server to start
    yield port_holder[0]
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2.0)

def _mqtt_connect(port: int, client_id: str) -> socket.socket:
    s = socket.socket()
    s.connect(("127.0.0.1", port))
    s.send(_build_connect_raw(client_id))
    s.recv(1024)  # CONNACK
    return s

def _build_subscribe(topic: str, packet_id: int = 1, qos: int = 0) -> bytes:
    from src.qdap.broker.packet_parser import encode_string, encode_remaining_length
    body = struct.pack("!H", packet_id) + bytes([0])
    body += encode_string(topic) + bytes([qos])
    return bytes([0x82]) + encode_remaining_length(len(body)) + body

@pytest.mark.integration
def test_broker_connect(running_broker):
    port = running_broker
    s = _mqtt_connect(port, "test1")
    assert s is not None
    s.close()

@pytest.mark.integration
def test_broker_subscribe_publish(running_broker):
    port = running_broker
    sub = _mqtt_connect(port, "subscriber")
    pub = _mqtt_connect(port, "publisher")

    sub.send(_build_subscribe("test/topic"))
    sub.recv(1024)  # SUBACK

    from src.qdap.broker.packet_parser import build_publish
    pub.send(build_publish("test/topic", b"hello"))
    time.sleep(0.1)

    sub.settimeout(1.0)
    data = sub.recv(1024)
    from src.qdap.broker.packet_parser import parse_packet
    pkt = parse_packet(data)
    assert pkt.ptype == MQTT_PUBLISH
    assert pkt.payload == b"hello"
    sub.close(); pub.close()

@pytest.mark.integration
def test_broker_wildcard_delivery(running_broker):
    port = running_broker
    sub = _mqtt_connect(port, "wild_sub")
    pub = _mqtt_connect(port, "wild_pub")

    sub.send(_build_subscribe("sensor/#"))
    sub.recv(1024)

    from src.qdap.broker.packet_parser import build_publish
    pub.send(build_publish("sensor/room1/temp", b"23.1"))
    time.sleep(0.2)

    sub.settimeout(1.0)
    data = sub.recv(1024)
    pkt = parse_packet(data)
    assert pkt.payload == b"23.1"
    sub.close(); pub.close()
