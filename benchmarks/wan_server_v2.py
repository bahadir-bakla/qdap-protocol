#!/usr/bin/env python3
"""
QDAP WAN Benchmark Server v2 — Ireland (eu-west-1)
====================================================
Protokoller:
  :19876  QDAP (fire-and-forget, GhostSession)
  :18801  HTTP/1.1 (aiohttp)
  :18802  WebSocket (websockets)
  :18803  gRPC (fixed — raw bytes echo via GenericRpcHandler)
  :18804  HTTP/2 (hypercorn + ASGI)
  :1883   MQTT broker via mosquitto (systemd); echo bridge in-process
  :18807  Large-file HTTP endpoint (POST → echo body size)
  :18900  Stats + reset (HTTP)

Kurulum (EC2 Ubuntu):
  sudo apt-get install -y mosquitto mosquitto-clients
  sudo systemctl enable --now mosquitto
  pip install aiohttp websockets grpcio hypercorn paho-mqtt
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_counts: dict[str, int] = {
    "QDAP": 0, "HTTP1": 0, "HTTP2": 0, "WS": 0, "gRPC": 0, "MQTT": 0, "LARGEFILE": 0,
}
_bytes_rx: dict[str, int] = {k: 0 for k in _counts}


# ── 1. QDAP ──────────────────────────────────────────────────────────────────

async def run_qdap() -> None:
    from qdap.server import QDAPServer
    server = QDAPServer(host="0.0.0.0", port=19876)

    def _on_frame(frame, addr):
        _counts["QDAP"] += 1

    server.on_frame(_on_frame)
    await server.start()
    print("[QDAP]      :19876 — ready")
    while True:
        await asyncio.sleep(3600)


# ── 2. HTTP/1.1 ──────────────────────────────────────────────────────────────

async def run_http1() -> None:
    from aiohttp import web

    async def handle(request: web.Request) -> web.Response:
        data = await request.read()
        _counts["HTTP1"] += 1
        _bytes_rx["HTTP1"] += len(data)
        return web.Response(body=data, status=200)

    app = web.Application()
    app.router.add_post("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 18801).start()
    print("[HTTP/1.1]  :18801 — ready")


# ── 3. WebSocket ─────────────────────────────────────────────────────────────

async def run_ws() -> None:
    import websockets

    async def handler(ws):
        async for msg in ws:
            _counts["WS"] += 1
            _bytes_rx["WS"] += len(msg) if isinstance(msg, (bytes, str)) else 0
            await ws.send(msg)

    await websockets.serve(handler, "0.0.0.0", 18802)
    print("[WebSocket] :18802 — ready")


# ── 4. gRPC (fixed) ──────────────────────────────────────────────────────────

async def run_grpc() -> None:
    import grpc
    import grpc.aio

    async def echo_handler(request: bytes, context) -> bytes:
        _counts["gRPC"] += 1
        _bytes_rx["gRPC"] += len(request) if request else 0
        return request

    class EchoHandler(grpc.GenericRpcHandler):
        def service(self, handler_call_details):
            return grpc.unary_unary_rpc_method_handler(
                echo_handler,
                request_deserializer=lambda x: x,
                response_serializer=lambda x: x,
            )

    srv = grpc.aio.server()
    srv.add_generic_rpc_handlers([EchoHandler()])
    srv.add_insecure_port("0.0.0.0:18803")
    await srv.start()
    print("[gRPC]      :18803 — ready")
    await srv.wait_for_termination()


# ── 5. HTTP/2 (hypercorn + raw ASGI) ─────────────────────────────────────────

async def run_http2() -> None:
    """HTTP/2 server via hypercorn (ASGI, h2 protocol)."""
    try:
        import hypercorn.asyncio
        import hypercorn.config

        async def app(scope, receive, send):
            if scope["type"] == "http":
                body = b""
                while True:
                    event = await receive()
                    body += event.get("body", b"")
                    if not event.get("more_body"):
                        break
                _counts["HTTP2"] += 1
                _bytes_rx["HTTP2"] += len(body)
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())],
                })
                await send({"type": "http.response.body", "body": body})

        cfg = hypercorn.config.Config()
        cfg.bind = ["0.0.0.0:18804"]
        cfg.alpn_protocols = ["h2", "http/1.1"]
        cfg.loglevel = "WARNING"
        await hypercorn.asyncio.serve(app, cfg)
    except ImportError:
        print("[HTTP/2]    :18804 — SKIPPED (hypercorn not installed)")
    except Exception as e:
        print(f"[HTTP/2]    :18804 — ERROR: {e}")


# ── 6. MQTT echo bridge (subscribes req → publishes res) ─────────────────────

async def run_mqtt_echo() -> None:
    """
    MQTT echo bridge: subscribes to 'qdap/bench/req', republishes to 'qdap/bench/res'.
    mosquitto must be running on localhost:1883.
    Uses paho-mqtt in a thread (paho is synchronous).
    """
    try:
        import paho.mqtt.client as mqtt
        import threading

        def _mqtt_thread():
            def on_connect(c, userdata, flags, rc):
                if rc == 0:
                    c.subscribe("qdap/bench/req", qos=1)
                    print("[MQTT echo]  :1883 — subscribed qdap/bench/req → echo to qdap/bench/res")

            def on_message(c, userdata, msg):
                _counts["MQTT"] += 1
                _bytes_rx["MQTT"] += len(msg.payload)
                c.publish("qdap/bench/res", msg.payload, qos=1)

            c = mqtt.Client(client_id="qdap-server-echo")
            c.on_connect = on_connect
            c.on_message = on_message
            try:
                c.connect("127.0.0.1", 1883, keepalive=60)
                print("[MQTT echo]  connecting to local mosquitto...")
                c.loop_forever()
            except Exception as e:
                print(f"[MQTT echo]  SKIPPED — mosquitto not running: {e}")

        t = threading.Thread(target=_mqtt_thread, daemon=True)
        t.start()
        await asyncio.sleep(2)
        print("[MQTT echo]  :1883 bridge thread started")
        while True:
            await asyncio.sleep(3600)
    except ImportError:
        print("[MQTT echo]  SKIPPED (paho-mqtt not installed)")
        while True:
            await asyncio.sleep(3600)


# ── 7. Large-file HTTP endpoint ───────────────────────────────────────────────

async def run_largefile() -> None:
    from aiohttp import web

    async def handle_large(request: web.Request) -> web.Response:
        data = await request.read()
        _counts["LARGEFILE"] += 1
        _bytes_rx["LARGEFILE"] += len(data)
        # Echo back size as confirmation
        return web.Response(text=str(len(data)), status=200)

    app = web.Application(client_max_size=64 * 1024 * 1024)  # 64MB max
    app.router.add_post("/upload", handle_large)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 18807).start()
    print("[LargeFile] :18807 — ready (POST /upload, max 64MB)")


# ── 7. Stats ─────────────────────────────────────────────────────────────────

async def run_stats() -> None:
    from aiohttp import web

    async def get_stats(request: web.Request) -> web.Response:
        return web.Response(
            text=json.dumps({"counts": _counts, "bytes": _bytes_rx}),
            content_type="application/json",
        )

    async def reset_stats(request: web.Request) -> web.Response:
        for k in _counts:
            _counts[k] = 0
            _bytes_rx[k] = 0
        return web.Response(text="reset")

    app = web.Application()
    app.router.add_get("/stats", get_stats)
    app.router.add_post("/reset", reset_stats)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 18900).start()
    print("[Stats]     :18900 — /stats /reset")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 60)
    print("QDAP WAN Benchmark Server v2 — all protocols")
    print("=" * 60)
    await asyncio.gather(
        run_qdap(),
        run_http1(),
        run_ws(),
        run_grpc(),
        run_http2(),
        run_mqtt_echo(),
        run_largefile(),
        run_stats(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
