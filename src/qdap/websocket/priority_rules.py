# src/qdap/websocket/priority_rules.py

"""
WebSocket mesaj içeriğine göre QDAP priority belirle.
JSON mesajlarda "type" alanına bakılır.
"""

# message type → (priority, deadline_ms)
TYPE_PRIORITY: dict[str, tuple[int, float]] = {
    "emergency":  (1000, 10.0),
    "alarm":      (1000, 10.0),
    "alert":      (900,  20.0),
    "audio":      (850,  50.0),
    "video":      (800,  100.0),
    "sensor":     (500,  500.0),
    "telemetry":  (500,  500.0),
    "command":    (700,  100.0),
    "data":       (300,  2000.0),
    "log":        (100,  5000.0),
    "ping":       (50,   10000.0),
}

DEFAULT_PRIORITY = (200, 500.0)


def message_to_priority(raw: bytes | str) -> tuple[int, float]:
    """
    Ham WebSocket mesajından (priority, deadline_ms) belirle.
    JSON ise "type" alanına bakılır.
    Binary ise default döner.
    """
    import json
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        msg_type = str(data.get("type", "")).lower()
        # Prefix match
        for k, v in TYPE_PRIORITY.items():
            if msg_type.startswith(k):
                return v
    except Exception:
        pass
    return DEFAULT_PRIORITY
