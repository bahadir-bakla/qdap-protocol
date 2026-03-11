# src/qdap/broker/qdap_transport.py
"""
Emergency topic pattern → QFrame priority=1000
Normal topic           → QFrame priority via QFT scheduler
"""
import re
from typing import Optional

EMERGENCY_PATTERNS = [
    re.compile(r"emergency", re.IGNORECASE),
    re.compile(r"alert"),
    re.compile(r"alarm"),
    re.compile(r"critical"),
    re.compile(r"^ems/"),
    re.compile(r"^hospital/"),
]

def topic_to_priority(topic: str, payload_size: int) -> int:
    for pattern in EMERGENCY_PATTERNS:
        if pattern.search(topic):
            return 1000
    # Küçük payload → yüksek öncelik (kontrol mesajı)
    if payload_size < 64:
        return 800
    elif payload_size < 1024:
        return 500
    else:
        return 100

def wrap_in_qframe(topic: str, payload: bytes) -> bytes:
    """
    Gerçek implementasyonda qdap_core.qframe_serialize kullanılır.
    Şimdilik passthrough + priority header.
    """
    priority = topic_to_priority(topic, len(payload))
    # 2-byte priority header + payload
    import struct
    return struct.pack("!H", priority) + payload

def unwrap_qframe(data: bytes) -> tuple:
    import struct
    priority = struct.unpack_from("!H", data, 0)[0]
    return priority, data[2:]
