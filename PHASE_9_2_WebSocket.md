# PHASE 9.2 — WebSocket Adapter: Browser → QDAP Bridge
## Tarayıcıdan Doğrudan QDAP Ghost Session
## Tahmini Süre: 1 hafta | Zorluk: Orta | Önkoşul: Phase 9.1

---

## Hedef

```
Browser (WebSocket) → QDAP WS Adapter → QDAP Ghost Session → Server

Kullanım:
  // Browser JS — değişiklik yok
  const ws = new WebSocket("ws://localhost:8765")
  ws.send(JSON.stringify({type: "sensor", data: 42}))

  → Altta QDAP Ghost Session
  → Priority: message type'a göre otomatik
```

---

## Proje Yapısı

```
src/qdap/websocket/
├── __init__.py
├── ws_adapter.py     ← WebSocket → QDAP bridge
└── priority_rules.py ← message type → priority

tests/
└── test_ws_adapter.py
```

---

## ADIM 1 — priority_rules.py

```python
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
```

---

## ADIM 2 — ws_adapter.py

```python
# src/qdap/websocket/ws_adapter.py

"""
WebSocket ↔ QDAP Ghost Session bridge.

Kullanım:
  python -m qdap.websocket.ws_adapter \
    --ws-port 8765 \
    --qdap-host remote.server.com \
    --qdap-port 19601

Browser bağlantısı:
  const ws = new WebSocket("ws://localhost:8765")
  ws.send('{"type": "sensor", "value": 42}')
"""

import asyncio
import argparse
import json
import logging
import websockets
from .priority_rules import message_to_priority

log = logging.getLogger("qdap.ws")


async def handle_websocket(
    websocket,
    qdap_host:    str,
    qdap_port:    int,
    use_security: bool,
):
    """Tek bir WebSocket bağlantısını QDAP'a köprüle."""
    client_id = id(websocket)
    log.info(f"WS bağlandı: client_{client_id}")

    # QDAP session kur
    try:
        if use_security:
            from qdap.session.secure_ghost_session import SecureGhostSession as S
        else:
            from qdap.session.ghost_session import GhostSession as S

        r, w = await asyncio.wait_for(
            asyncio.open_connection(qdap_host, qdap_port),
            timeout=10.0,
        )
        session = S(r, w)
        if use_security:
            await session.perform_handshake(is_client=True)

    except Exception as e:
        log.error(f"QDAP bağlantı hatası: {e}")
        await websocket.close(1011, "QDAP connection failed")
        return

    # İki yönlü köprü: WS → QDAP ve QDAP → WS
    async def ws_to_qdap():
        """WebSocket'ten gelen mesajları QDAP'a gönder."""
        async for message in websocket:
            raw = message if isinstance(message, bytes) else message.encode()
            priority, deadline_ms = message_to_priority(raw)
            log.debug(
                f"WS→QDAP len={len(raw)} "
                f"priority={priority} deadline={deadline_ms}ms"
            )
            await session.send(raw, priority=priority, deadline_ms=deadline_ms)

    async def qdap_to_ws():
        """QDAP'tan gelen mesajları WebSocket'e gönder."""
        while True:
            try:
                data = await asyncio.wait_for(session.receive(), timeout=60.0)
                if data:
                    await websocket.send(data)
            except asyncio.TimeoutError:
                # Keepalive ping
                await websocket.ping()
            except Exception:
                break

    try:
        await asyncio.gather(ws_to_qdap(), qdap_to_ws())
    except websockets.ConnectionClosed:
        log.info(f"WS bağlantı kesildi: client_{client_id}")
    finally:
        w.close()


async def start_server(
    ws_port:      int,
    qdap_host:    str,
    qdap_port:    int,
    use_security: bool,
):
    handler = lambda ws: handle_websocket(
        ws, qdap_host, qdap_port, use_security
    )

    async with websockets.serve(handler, "0.0.0.0", ws_port):
        log.info(f"✅ QDAP WebSocket Adapter → ws://0.0.0.0:{ws_port}")
        log.info(f"   QDAP target: {qdap_host}:{qdap_port}")
        log.info(f"   Security: {'ON (X25519+AES)' if use_security else 'OFF'}")
        await asyncio.Future()  # çalışmaya devam et


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ws-port",     type=int, default=8765)
    parser.add_argument("--qdap-host",   required=True)
    parser.add_argument("--qdap-port",   type=int, default=19601)
    parser.add_argument("--no-security", action="store_true")
    parser.add_argument("--verbose",     action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO
    )
    asyncio.run(start_server(
        args.ws_port, args.qdap_host, args.qdap_port,
        not args.no_security,
    ))


if __name__ == "__main__":
    main()
```

---

## ADIM 3 — Testler

```python
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
```

---

## Browser Demo (HTML)

```html
<!-- examples/ws_demo.html -->
<!DOCTYPE html>
<html>
<head><title>QDAP WebSocket Demo</title></head>
<body>
<h2>QDAP WebSocket Bridge Demo</h2>

<button onclick="sendSensor()">📡 Sensor (priority=500)</button>
<button onclick="sendEmergency()">🚨 Emergency (priority=1000)</button>
<button onclick="sendLog()">📝 Log (priority=100)</button>

<pre id="log"></pre>

<script>
const ws = new WebSocket("ws://localhost:8765");
const log = document.getElementById("log");

ws.onmessage = e => {
  log.textContent += "← " + e.data + "\n";
};

function send(type, data) {
  const msg = JSON.stringify({type, ...data, ts: Date.now()});
  ws.send(msg);
  log.textContent += "→ " + msg + "\n";
}

const sendSensor    = () => send("sensor",    {value: Math.random()});
const sendEmergency = () => send("emergency", {alert: "FIRE!"});
const sendLog       = () => send("log",       {msg: "debug info"});
</script>
</body>
</html>
```

---

## requirements.txt Eklentisi

```
websockets>=12.0
```

---

## Teslim Kriterleri

```
✅ src/qdap/websocket/ modülü oluşturuldu
✅ pip install websockets
✅ python -m qdap.websocket.ws_adapter --help çalışıyor
✅ 235 mevcut test HÂLÂ geçiyor
✅ tests/test_ws_adapter.py → 6 yeni test geçiyor
✅ Toplam: 241+ test

DOKUNMA:
  ❌ Mevcut hiçbir şeye dokunma
  ❌ Sadece src/qdap/websocket/ ve tests/ ekle
```
