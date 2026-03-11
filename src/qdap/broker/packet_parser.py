# src/qdap/broker/packet_parser.py
import struct
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

MQTT_CONNECT     = 1
MQTT_CONNACK     = 2
MQTT_PUBLISH     = 3
MQTT_PUBACK      = 4
MQTT_SUBSCRIBE   = 8
MQTT_SUBACK      = 9
MQTT_UNSUBSCRIBE = 10
MQTT_UNSUBACK    = 11
MQTT_PINGREQ     = 12
MQTT_PINGRESP    = 13
MQTT_DISCONNECT  = 14

@dataclass
class MQTTPacket:
    ptype: int
    flags: int = 0
    payload: bytes = b""
    # PUBLISH fields
    topic: str = ""
    qos: int = 0
    retain: bool = False
    packet_id: Optional[int] = None
    # CONNECT fields
    client_id: str = ""
    clean_session: bool = True
    keepalive: int = 60
    # SUBSCRIBE fields
    subscriptions: List[Tuple[str, int]] = field(default_factory=list)

def decode_remaining_length(data: bytes, offset: int) -> Tuple[int, int]:
    """Returns (length, new_offset)"""
    multiplier = 1
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value += (byte & 0x7F) * multiplier
        multiplier *= 128
        if not (byte & 0x80):
            break
    return value, offset

def encode_remaining_length(length: int) -> bytes:
    result = []
    while True:
        byte = length % 128
        length //= 128
        if length > 0:
            byte |= 0x80
        result.append(byte)
        if length == 0:
            break
    return bytes(result)

def decode_string(data: bytes, offset: int) -> Tuple[str, int]:
    length = struct.unpack_from("!H", data, offset)[0]
    offset += 2
    return data[offset:offset+length].decode("utf-8"), offset + length

def encode_string(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded

def parse_packet(data: bytes) -> Optional[MQTTPacket]:
    if len(data) < 2:
        return None
    first_byte = data[0]
    ptype = (first_byte >> 4) & 0x0F
    flags = first_byte & 0x0F
    remaining_length, offset = decode_remaining_length(data, 1)

    pkt = MQTTPacket(ptype=ptype, flags=flags)
    body = data[offset:offset + remaining_length]

    if ptype == MQTT_CONNECT:
        # Protocol name
        proto_len = struct.unpack_from("!H", body, 0)[0]
        pos = 2 + proto_len + 1  # skip proto name + version
        connect_flags = body[pos]
        pos += 1
        pkt.keepalive = struct.unpack_from("!H", body, pos)[0]
        pos += 2
        # MQTT 5.0: properties length
        prop_len = body[pos]; pos += 1 + prop_len
        pkt.client_id, pos = decode_string(body, pos)
        pkt.clean_session = bool(connect_flags & 0x02)

    elif ptype == MQTT_PUBLISH:
        qos = (flags >> 1) & 0x03
        retain = bool(flags & 0x01)
        pkt.qos = qos
        pkt.retain = retain
        pkt.topic, pos = decode_string(body, 0)
        if qos > 0:
            pkt.packet_id = struct.unpack_from("!H", body, pos)[0]
            pos += 2
        
        prop_len, pos = decode_remaining_length(body, pos)
        pos += prop_len
        pkt.payload = body[pos:]

    elif ptype == MQTT_SUBSCRIBE:
        pkt.packet_id = struct.unpack_from("!H", body, 0)[0]
        pos = 3  # skip packet_id + properties length
        subs = []
        while pos < len(body):
            topic, pos = decode_string(body, pos)
            qos = body[pos] & 0x03
            pos += 1
            subs.append((topic, qos))
        pkt.subscriptions = subs

    elif ptype == MQTT_UNSUBSCRIBE:
        pkt.packet_id = struct.unpack_from("!H", body, 0)[0]
        pos = 3
        topics = []
        while pos < len(body):
            topic, pos = decode_string(body, pos)
            topics.append((topic, 0))
        pkt.subscriptions = topics

    return pkt

def build_connack(session_present: bool = False, reason_code: int = 0) -> bytes:
    payload = bytes([1 if session_present else 0, reason_code, 0])  # +properties len
    return bytes([0x20]) + encode_remaining_length(len(payload)) + payload

def build_suback(packet_id: int, reason_codes: List[int]) -> bytes:
    body = struct.pack("!H", packet_id) + bytes([0]) + bytes(reason_codes)
    return bytes([0x90]) + encode_remaining_length(len(body)) + body

def build_unsuback(packet_id: int, count: int) -> bytes:
    body = struct.pack("!H", packet_id) + bytes([0]) + bytes([0] * count)
    return bytes([0xB0]) + encode_remaining_length(len(body)) + body

def build_puback(packet_id: int) -> bytes:
    body = struct.pack("!H", packet_id) + bytes([0, 0])
    return bytes([0x40]) + encode_remaining_length(len(body)) + body

def build_publish(topic: str, payload: bytes, qos: int = 0,
                  packet_id: Optional[int] = None, retain: bool = False) -> bytes:
    flags = (qos << 1) | (1 if retain else 0)
    body = encode_string(topic)
    if qos > 0 and packet_id is not None:
        body += struct.pack("!H", packet_id)
    body += bytes([0])  # properties length
    body += payload
    return bytes([0x30 | flags]) + encode_remaining_length(len(body)) + body

PINGRESP = bytes([0xD0, 0x00])
