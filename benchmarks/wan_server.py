#!/usr/bin/env python3
"""
QDAP WAN Benchmark — Server
============================
Ireland (eu-west-1) EC2 instance'inda calisir.
Tum protokol sunucularini baslatir, istatistik endpoint'i sunar.

Calistirma:
  python3 wan_server.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Ortak sayac ───────────────────────────────────────────────────────────────

_counts: dict[str, int] = {
    "QDAP": 0, "HTTP": 0, "WS": 0, "gRPC": 0,
}


# ── 1. QDAP ──────────────────────────────────────────────────────────────────

async def run_qdap():
    from qdap.server import QDAPServer

    server = QDAPServer(host="0.0.0.0", port=19876)

    def _on_frame(frame, addr):
        _counts["QDAP"] += 1

    server.on_frame(_on_frame)
    await server.start()
    print("[QDAP]    :19876 — ready")
    # Keep server alive
    while True:
        await asyncio.sleep(3600)


# ── 2. HTTP/1.1 (aiohttp) ────────────────────────────────────────────────────

async def run_http():
    from aiohttp import web

    async def handle(request: web.Request) -> web.Response:
        data = await request.read()
        _counts["HTTP"] += 1
        return web.Response(body=data, status=200)

    app = web.Application()
    app.router.add_post("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 18801).start()
    print("[HTTP/1.1] :18801 — ready")


# ── 3. WebSocket ─────────────────────────────────────────────────────────────

async def run_ws():
    import websockets

    async def handler(ws):
        async for msg in ws:
            _counts["WS"] += 1
            await ws.send(msg)

    await websockets.serve(handler, "0.0.0.0", 18802)
    print("[WebSocket] :18802 — ready")


# ── 4. gRPC ──────────────────────────────────────────────────────────────────

async def run_grpc():
    import grpc
    import grpc.aio

    class EchoServicer(grpc.GenericRpcHandler):
        def service(self, handler_call_details):
            return grpc.unary_unary_rpc_method_handler(
                self._echo,
                request_deserializer=lambda x: x,
                response_serializer=lambda x: x,
            )

        async def _echo(self, request, context):
            _counts["gRPC"] += 1
            return request

    srv = grpc.aio.server()
    srv.add_generic_rpc_handlers([EchoServicer()])
    srv.add_insecure_port("0.0.0.0:18803")
    await srv.start()
    print("[gRPC]     :18803 — ready")


# ── 5. Stats HTTP (istemcinin sayac cekebilmesi icin) ────────────────────────

async def run_stats():
    from aiohttp import web

    async def get_stats(request: web.Request) -> web.Response:
        return web.Response(
            text=json.dumps(_counts),
            content_type="application/json",
        )

    async def reset_stats(request: web.Request) -> web.Response:
        for k in _counts:
            _counts[k] = 0
        return web.Response(text="reset")

    app = web.Application()
    app.router.add_get("/stats", get_stats)
    app.router.add_post("/reset", reset_stats)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 18900).start()
    print("[Stats]    :18900 — /stats /reset")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 50)
    print("QDAP WAN Benchmark Server — starting all protocols")
    print("=" * 50)

    await asyncio.gather(
        run_qdap(),
        run_http(),
        run_ws(),
        run_grpc(),
        run_stats(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
