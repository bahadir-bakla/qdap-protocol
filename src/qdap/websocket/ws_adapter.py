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
                    await websocket.send(data.decode('utf-8') if isinstance(data, bytes) else data)
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
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(start_server(
        args.ws_port, args.qdap_host, args.qdap_port,
        not args.no_security,
    ))


if __name__ == "__main__":
    main()
