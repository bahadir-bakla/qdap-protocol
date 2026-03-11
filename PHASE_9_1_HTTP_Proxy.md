# PHASE 9.1 — HTTP Proxy: Drop-In Deployment
## Mevcut HTTP/REST Trafiğini Sıfır Değişiklikle QDAP'a Taşı
## Tahmini Süre: 3 hafta | Zorluk: Orta | Önkoşul: Yok

---

## Hedef

```
[Client HTTP] → [QDAP Proxy :8080] ──QDAP Ghost──→ [QDAP Proxy :8082] → [Server HTTP]

Sıfır client değişikliği:
  curl http://localhost:8080/api/data   ← aynen çalışır
  → Altta QDAP Ghost Session + AES-256-GCM
  → Emergency topic → priority=1000, deadline=10ms
```

---

## Proje Yapısı (Yeni Dosyalar)

```
src/qdap/proxy/
├── __init__.py
├── http_bridge.py       ← HTTP ↔ QFrame dönüşümü
├── proxy_server.py      ← aiohttp sunucu
└── priority_mapper.py   ← Content-Type → priority

tests/
└── test_http_proxy.py   ← yeni testler

examples/
└── proxy_demo.sh        ← nginx demo
```

---

## ADIM 1 — priority_mapper.py

```python
# src/qdap/proxy/priority_mapper.py

PRIORITY_MAP = {
    "audio/":                   1000,
    "video/":                    900,
    "application/x-emergency":   950,
    "image/":                    500,
    "application/json":          300,
    "application/xml":           300,
    "text/":                     100,
    "application/":              200,
    "*":                         200,
}

DEADLINE_MAP = {
    "audio/":                    50.0,
    "video/":                   100.0,
    "application/x-emergency":   10.0,
    "image/":                   500.0,
    "text/":                   2000.0,
    "*":                        500.0,
}


def content_type_to_priority(
    content_type: str,
    headers: dict,
) -> tuple[int, float]:
    """
    Content-Type ve HTTP header'lardan (priority, deadline_ms) döndür.
    X-QDAP-Priority header varsa override eder.
    """
    if "X-QDAP-Priority" in headers:
        try:
            return (
                int(headers["X-QDAP-Priority"]),
                float(headers.get("X-QDAP-Deadline-Ms", 500.0)),
            )
        except ValueError:
            pass

    ct = (content_type or "").lower()
    for prefix, priority in PRIORITY_MAP.items():
        if prefix == "*":
            continue
        if ct.startswith(prefix):
            return priority, DEADLINE_MAP.get(prefix, 500.0)

    return PRIORITY_MAP["*"], DEADLINE_MAP["*"]
```

---

## ADIM 2 — http_bridge.py

```python
# src/qdap/proxy/http_bridge.py

import base64
import json
from .priority_mapper import content_type_to_priority


def http_to_qdap_payload(
    method:  str,
    path:    str,
    headers: dict,
    body:    bytes,
) -> tuple[bytes, int, float]:
    """
    HTTP request → (QDAP payload bytes, priority, deadline_ms)

    Payload formatı (JSON envelope):
    {
        "method":   "POST",
        "path":     "/api/data",
        "headers":  {...},
        "body_b64": "<base64>"
    }
    """
    content_type          = headers.get("Content-Type", "")
    priority, deadline_ms = content_type_to_priority(content_type, headers)

    envelope = {
        "method":   method,
        "path":     path,
        "headers":  {k: v for k, v in headers.items()
                     if not k.startswith("X-QDAP-")},
        "body_b64": base64.b64encode(body).decode(),
    }

    payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return payload, priority, deadline_ms


def qdap_payload_to_http(payload: bytes) -> tuple[str, str, dict, bytes]:
    """QDAP payload → (method, path, headers, body)"""
    envelope = json.loads(payload.decode("utf-8"))
    return (
        envelope["method"],
        envelope["path"],
        envelope["headers"],
        base64.b64decode(envelope["body_b64"]),
    )


def build_response_payload(
    status:  int,
    headers: dict,
    body:    bytes,
) -> bytes:
    """HTTP response → QDAP payload"""
    return json.dumps({
        "status":   status,
        "headers":  headers,
        "body_b64": base64.b64encode(body).decode(),
    }).encode("utf-8")


def parse_response_payload(
    payload: bytes,
) -> tuple[int, dict, bytes]:
    """QDAP response payload → (status, headers, body)"""
    try:
        data = json.loads(payload.decode("utf-8"))
        return (
            data.get("status", 200),
            data.get("headers", {}),
            base64.b64decode(data.get("body_b64", "")),
        )
    except Exception:
        return 200, {}, payload
```

---

## ADIM 3 — proxy_server.py

```python
# src/qdap/proxy/proxy_server.py

"""
QDAP HTTP Proxy.

Kullanım:
  python -m qdap.proxy.proxy_server \
    --listen-port 8080 \
    --qdap-host <remote> \
    --qdap-port 19601 \
    --target-host localhost \
    --target-port 8081

İki mod:
  CLIENT mod: gelen HTTP'yi QDAP'a çevir, ileri gönder
  SERVER mod: gelen QDAP'ı HTTP'ye çevir, backend'e ilet
"""

import asyncio
import argparse
import logging
import struct
from aiohttp import web, ClientSession, ClientTimeout
from .http_bridge import (
    http_to_qdap_payload,
    qdap_payload_to_http,
    build_response_payload,
    parse_response_payload,
)

log = logging.getLogger("qdap.proxy")


class QDAPHTTPProxy:

    def __init__(
        self,
        listen_port:  int,
        qdap_host:    str,
        qdap_port:    int,
        target_host:  str,
        target_port:  int,
        use_security: bool = True,
        mode:         str  = "client",  # "client" | "server"
    ):
        self.listen_port  = listen_port
        self.qdap_host    = qdap_host
        self.qdap_port    = qdap_port
        self.target_host  = target_host
        self.target_port  = target_port
        self.use_security = use_security
        self.mode         = mode

    # ── CLIENT MODE: HTTP gelen → QDAP giden ──────────────────────────

    async def handle_client_request(
        self, request: web.Request
    ) -> web.Response:
        """HTTP request al → QDAP'a gönder → cevabı döndür."""
        try:
            body    = await request.read()
            headers = dict(request.headers)

            payload, priority, deadline_ms = http_to_qdap_payload(
                method  = request.method,
                path    = request.path_qs,
                headers = headers,
                body    = body,
            )

            log.debug(
                f"{request.method} {request.path} "
                f"priority={priority} deadline={deadline_ms}ms"
            )

            resp_payload = await self._send_qdap(payload, priority, deadline_ms)
            if resp_payload is None:
                return web.Response(status=504, text="QDAP gateway timeout")

            status, resp_headers, resp_body = parse_response_payload(resp_payload)
            return web.Response(
                status  = status,
                headers = resp_headers,
                body    = resp_body,
            )

        except Exception as e:
            log.error(f"Proxy error: {e}")
            return web.Response(status=502, text=str(e))

    async def _send_qdap(
        self,
        payload:     bytes,
        priority:    int,
        deadline_ms: float,
    ) -> bytes | None:
        """Payload'ı QDAP üzerinden gönder, cevap bekle."""
        try:
            if self.use_security:
                from qdap.session.secure_ghost_session import SecureGhostSession as S
            else:
                from qdap.session.ghost_session import GhostSession as S

            r, w = await asyncio.wait_for(
                asyncio.open_connection(self.qdap_host, self.qdap_port),
                timeout=10.0,
            )
            session = S(r, w)
            if self.use_security:
                await session.perform_handshake(is_client=True)

            await session.send(payload, priority=priority, deadline_ms=deadline_ms)

            timeout = max(deadline_ms * 3 / 1000.0, 5.0)
            response = await asyncio.wait_for(session.receive(), timeout=timeout)
            w.close()
            return response

        except asyncio.TimeoutError:
            log.warning(f"QDAP timeout (deadline={deadline_ms}ms)")
            return None
        except Exception as e:
            log.error(f"QDAP send error: {e}")
            return None

    # ── SERVER MODE: QDAP gelen → HTTP backend ────────────────────────

    async def handle_qdap_connection(self, r, w):
        """QDAP bağlantısını al → backend'e HTTP ile ilet."""
        try:
            if self.use_security:
                from qdap.session.secure_ghost_session import SecureGhostSession as S
            else:
                from qdap.session.ghost_session import GhostSession as S

            session = S(r, w)
            if self.use_security:
                await session.perform_handshake(is_client=False)

            payload = await session.receive()
            method, path, headers, body = qdap_payload_to_http(payload)

            # Backend'e HTTP ile ilet
            url = f"http://{self.target_host}:{self.target_port}{path}"
            async with ClientSession(timeout=ClientTimeout(total=30)) as http:
                async with http.request(
                    method, url, headers=headers, data=body
                ) as resp:
                    resp_body    = await resp.read()
                    resp_payload = build_response_payload(
                        resp.status, dict(resp.headers), resp_body
                    )

            await session.send(resp_payload)

        except Exception as e:
            log.error(f"Server handler error: {e}")
        finally:
            w.close()

    # ── Başlatma ──────────────────────────────────────────────────────

    async def start(self):
        if self.mode == "client":
            app = web.Application()
            app.router.add_route("*", "/{path:.*}", self.handle_client_request)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", self.listen_port)
            await site.start()
            log.info(f"✅ QDAP HTTP Proxy (CLIENT) → 0.0.0.0:{self.listen_port}")
        else:
            server = await asyncio.start_server(
                self.handle_qdap_connection, "0.0.0.0", self.listen_port
            )
            log.info(f"✅ QDAP HTTP Proxy (SERVER) → 0.0.0.0:{self.listen_port}")
            async with server:
                await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="QDAP HTTP Proxy")
    parser.add_argument("--listen-port",  type=int, default=8080)
    parser.add_argument("--qdap-host",    required=True)
    parser.add_argument("--qdap-port",    type=int, default=19601)
    parser.add_argument("--target-host",  default="localhost")
    parser.add_argument("--target-port",  type=int, default=8081)
    parser.add_argument("--mode",         default="client",
                        choices=["client", "server"])
    parser.add_argument("--no-security",  action="store_true")
    parser.add_argument("--verbose",      action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    proxy = QDAPHTTPProxy(
        listen_port  = args.listen_port,
        qdap_host    = args.qdap_host,
        qdap_port    = args.qdap_port,
        target_host  = args.target_host,
        target_port  = args.target_port,
        use_security = not args.no_security,
        mode         = args.mode,
    )

    asyncio.run(proxy.start())


if __name__ == "__main__":
    main()
```

---

## ADIM 4 — __init__.py

```python
# src/qdap/proxy/__init__.py
from .proxy_server import QDAPHTTPProxy
from .http_bridge import http_to_qdap_payload, qdap_payload_to_http
from .priority_mapper import content_type_to_priority

__all__ = [
    "QDAPHTTPProxy",
    "http_to_qdap_payload",
    "qdap_payload_to_http",
    "content_type_to_priority",
]
```

---

## ADIM 5 — Testler

```python
# tests/test_http_proxy.py

import pytest
from qdap.proxy.priority_mapper import content_type_to_priority
from qdap.proxy.http_bridge import (
    http_to_qdap_payload,
    qdap_payload_to_http,
    build_response_payload,
    parse_response_payload,
)


class TestPriorityMapper:

    def test_audio_high_priority(self):
        p, d = content_type_to_priority("audio/mpeg", {})
        assert p >= 900
        assert d <= 100.0

    def test_text_low_priority(self):
        p, d = content_type_to_priority("text/html", {})
        assert p <= 200
        assert d >= 500.0

    def test_x_qdap_priority_override(self):
        p, d = content_type_to_priority(
            "text/html",
            {"X-QDAP-Priority": "999", "X-QDAP-Deadline-Ms": "5.0"}
        )
        assert p == 999
        assert d == 5.0

    def test_emergency_content_type(self):
        p, _ = content_type_to_priority("application/x-emergency", {})
        assert p >= 950


class TestHTTPBridge:

    def test_roundtrip_get(self):
        payload, priority, deadline = http_to_qdap_payload(
            method="GET", path="/api/test",
            headers={"Content-Type": "application/json"},
            body=b"",
        )
        method, path, headers, body = qdap_payload_to_http(payload)
        assert method == "GET"
        assert path   == "/api/test"
        assert body   == b""

    def test_roundtrip_post_with_body(self):
        original_body = b'{"key": "value"}'
        payload, _, _ = http_to_qdap_payload(
            method="POST", path="/api/data",
            headers={"Content-Type": "application/json"},
            body=original_body,
        )
        _, _, _, body = qdap_payload_to_http(payload)
        assert body == original_body

    def test_qdap_headers_stripped(self):
        payload, _, _ = http_to_qdap_payload(
            method="GET", path="/",
            headers={
                "Content-Type": "text/html",
                "X-QDAP-Priority": "999",
            },
            body=b"",
        )
        _, _, headers, _ = qdap_payload_to_http(payload)
        assert "X-QDAP-Priority" not in headers

    def test_response_roundtrip(self):
        original = b"Hello World"
        payload  = build_response_payload(200, {"X-Test": "1"}, original)
        status, headers, body = parse_response_payload(payload)
        assert status         == 200
        assert headers["X-Test"] == "1"
        assert body           == original

    def test_binary_body(self):
        import os
        binary = os.urandom(1024)
        payload, _, _ = http_to_qdap_payload(
            "POST", "/upload",
            {"Content-Type": "application/octet-stream"},
            binary,
        )
        _, _, _, body = qdap_payload_to_http(payload)
        assert body == binary
```

---

## ADIM 6 — Demo Script

```bash
#!/bin/bash
# examples/proxy_demo.sh
# Senaryo: curl → QDAP Proxy → nginx
#
# Kurulum:
#   docker run -d -p 8083:80 nginx
#   python -m qdap.proxy.proxy_server \
#       --listen-port 8080 \
#       --qdap-host localhost \
#       --qdap-port 19601 \
#       --mode client &
#   python -m qdap.proxy.proxy_server \
#       --listen-port 8082 \
#       --qdap-host localhost \
#       --qdap-port 19601 \
#       --target-host localhost \
#       --target-port 8083 \
#       --mode server &
#
# Test:
#   curl http://localhost:8080/          # Normal HTTP
#   curl -H "Content-Type: audio/mpeg" http://localhost:8080/  # High priority
#   curl -H "X-QDAP-Priority: 999" http://localhost:8080/      # Emergency

echo "QDAP HTTP Proxy Demo"
echo "Normal:    $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/)"
echo "Audio:     $(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: audio/mpeg' http://localhost:8080/)"
echo "Emergency: $(curl -s -o /dev/null -w '%{http_code}' -H 'X-QDAP-Priority: 999' http://localhost:8080/)"
```

---

## Teslim Kriterleri

```
✅ src/qdap/proxy/ modülü oluşturuldu (4 dosya)
✅ python -m qdap.proxy.proxy_server --help çalışıyor
✅ 226 mevcut test HÂLÂ geçiyor
✅ tests/test_http_proxy.py → 9 yeni test geçiyor
✅ Toplam: 235+ test

Demo doğrulama:
  curl → :8080 → QDAP → :8082 → nginx
  HTTP 200 dönmeli

DOKUNMA:
  ❌ src/qdap/session/ → değişmez
  ❌ docker_benchmark/ → değişmez
  ❌ Mevcut testler → değişmez
```

---

## Paper'a Katkısı

```
Section 6.1 — HTTP Proxy Deployment:

"QDAP-Proxy provides a transparent HTTP/1.1 bridge
requiring zero client-side changes. Content-Type headers
are mapped to QDAP priority levels automatically,
enabling priority-aware delivery for audio (priority=1000),
images (priority=500), and text (priority=100) without
application modification."
```
