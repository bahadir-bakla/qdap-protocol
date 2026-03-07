#!/usr/bin/env python3
"""
İki QUIC server + bir TCP stats server çalıştırır:
  Port 19700/UDP: QUIC ACK server (baseline)
  Port 19701/UDP: QDAP QUIC server (Ghost Session, ACK yok)
  Port 19702/TCP: Stats server (kaç mesaj alındı?)
"""

import asyncio
import json
import logging
import pathlib
import struct

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived, QuicEvent

logging.getLogger("aioquic").setLevel(logging.ERROR)

CERT_PATH = pathlib.Path(__file__).parent.parent / "certs" / "cert.pem"
KEY_PATH  = pathlib.Path(__file__).parent.parent / "certs" / "key.pem"

# Global sayaç — QDAP QUIC server kaç mesaj aldı
_qdap_received_count = 0
_qdap_received_lock  = None   # asyncio.Lock, main'de init edilecek


# ── ACK Server (Baseline) ────────────────────────────────────────────────────

class QUICAckServerProtocol(QuicConnectionProtocol):
    """Her stream'e 8 byte ACK döner."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._acked_streams = set()

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived):
            if event.end_stream and event.stream_id not in self._acked_streams:
                self._acked_streams.add(event.stream_id)
                ack = struct.pack(">II", event.stream_id & 0xFFFFFFFF, 1)
                self._quic.send_stream_data(
                    stream_id=event.stream_id,
                    data=ack,
                    end_stream=True,
                )
            self.transmit()


# ── QDAP Ghost Session Server ────────────────────────────────────────────────

class QDAPQuicServerProtocol(QuicConnectionProtocol):
    """
    Veri alır, ACK GÖNDERMEZ.
    Her alınan mesajı global sayaca ekler.
    """

    def quic_event_received(self, event: QuicEvent) -> None:
        global _qdap_received_count
        if isinstance(event, StreamDataReceived):
            if event.end_stream:
                _qdap_received_count += 1
            self.transmit()


# ── TCP Stats Server ─────────────────────────────────────────────────────────

async def handle_stats_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """
    GET /stats → {"received": N} döner.
    Client bu endpoint'i poll ederek
    tüm paketlerin ulaşıp ulaşmadığını anlar.
    """
    global _qdap_received_count

    try:
        # HTTP-like request oku (basit)
        await reader.read(1024)   # "GET /stats\r\n\r\n" gibi bir şey

        response_body = json.dumps({"received": _qdap_received_count})
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(response_body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{response_body}"
        )
        writer.write(response.encode())
        await writer.drain()
    finally:
        writer.close()


async def reset_stats_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """POST /reset → sayacı sıfırla."""
    global _qdap_received_count
    await reader.read(1024)
    _qdap_received_count = 0
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()


async def stats_server_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """GET /stats veya POST /reset yönlendirici."""
    first_line = (await reader.read(20)).decode(errors="ignore")
    if "reset" in first_line.lower() or "POST" in first_line:
        await reset_stats_handler(reader, writer)
    else:
        await handle_stats_request(reader, writer)


# ── Server Başlatıcılar ───────────────────────────────────────────────────────

async def start_ack_server(host: str = "0.0.0.0", port: int = 19700):
    config = QuicConfiguration(alpn_protocols=["qdap-ack"], is_client=False)
    config.load_cert_chain(CERT_PATH, KEY_PATH)
    print(f"[QUIC ACK Server]   UDP {host}:{port}")
    await serve(host, port, configuration=config,
                create_protocol=QUICAckServerProtocol)


async def start_qdap_server(host: str = "0.0.0.0", port: int = 19701):
    config = QuicConfiguration(alpn_protocols=["qdap-ghost"], is_client=False)
    config.load_cert_chain(CERT_PATH, KEY_PATH)
    print(f"[QDAP QUIC Server]  UDP {host}:{port}")
    await serve(host, port, configuration=config,
                create_protocol=QDAPQuicServerProtocol)


async def start_stats_server(host: str = "0.0.0.0", port: int = 19702):
    server = await asyncio.start_server(stats_server_handler, host, port)
    print(f"[Stats TCP Server]  TCP {host}:{port}")
    async with server:
        await server.serve_forever()


async def main():
    global _qdap_received_lock
    _qdap_received_lock = asyncio.Lock()
    print("[Receiver] Starting all servers...")
    await asyncio.gather(
        start_ack_server(),
        start_qdap_server(),
        start_stats_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
