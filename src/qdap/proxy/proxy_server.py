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
            # Aiohttp router setup to handle all routes
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
