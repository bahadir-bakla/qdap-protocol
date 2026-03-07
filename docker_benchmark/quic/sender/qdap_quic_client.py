#!/usr/bin/env python3
"""
QDAP QUIC Ghost Session client — DÜZELTILMIŞ ölçüm.

Ölçüm yöntemi:
  1. t_start = şimdi
  2. Tüm mesajları QUIC üzerinden fire-and-forget gönder
  3. TCP stats endpoint'ini poll et: "server kaç aldı?"
  4. Server n_messages aldığında: t_end = şimdi
  5. duration = t_end - t_start  ← gerçek ağ zamanı dahil

QUIC data path: ACK YOK (Ghost Session) ✅
Ölçüm sync: ayrı TCP kanal (benchmark tool, paper claim'i bozmaz) ✅
"""

import asyncio
import json
import logging
import pathlib
import ssl
import time
from dataclasses import dataclass

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent

logging.getLogger("aioquic").setLevel(logging.ERROR)

CERT_PATH    = pathlib.Path(__file__).parent.parent / "certs" / "cert.pem"
STATS_HOST   = "172.20.0.10"
STATS_PORT   = 19702
POLL_INTERVAL = 0.05   # 50ms'de bir poll et


@dataclass
class QDAPQuicMetrics:
    protocol:        str   = "QDAP_QUIC_GhostSession"
    n_messages:      int   = 0
    n_received:      int   = 0   # server'ın aldığı
    payload_bytes:   int   = 0
    ack_bytes_sent:  int   = 0   # Her zaman 0
    throughput_mbps: float = 0.0
    p99_latency_ms:  float = 0.0
    duration_sec:    float = 0.0


class QDAPQuicGhostProtocol(QuicConnectionProtocol):
    """ACK beklemeyen QUIC client."""

    def quic_event_received(self, event: QuicEvent) -> None:
        pass   # Ghost Session — ACK dinlemiyoruz

    async def send_ghost(self, payload: bytes) -> None:
        """Mesajı gönder, devam et."""
        stream_id = self._quic.get_next_available_stream_id()
        self._quic.send_stream_data(
            stream_id=stream_id,
            data=payload,
            end_stream=True,
        )
        self.transmit()


async def _get_server_received_count(host: str, port: int) -> int:
    """
    TCP stats endpoint'inden server'ın kaç mesaj aldığını sorgula.
    Returns: received count veya -1 (hata)
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0,
        )
        writer.write(b"GET /stats HTTP/1.0\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        writer.close()

        # HTTP response body'yi parse et
        body = response.decode(errors="ignore").split("\r\n\r\n", 1)
        if len(body) < 2:
            return -1
        data = json.loads(body[1])
        return data.get("received", -1)
    except Exception:
        return -1


async def _reset_server_count(host: str, port: int) -> None:
    """Benchmark başlamadan sayacı sıfırla."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0,
        )
        writer.write(b"POST /reset HTTP/1.0\r\n\r\n")
        await writer.drain()
        await reader.read(256)
        writer.close()
    except Exception:
        pass


async def _wait_for_all_received(
    n_expected: int,
    host:       str,
    port:       int,
    timeout:    float = 120.0,
) -> tuple[int, float]:
    """
    Server n_expected mesajı alana kadar poll et.
    Returns: (actual_received, elapsed_seconds_when_done)
    """
    deadline = time.monotonic() + timeout
    t_start  = time.monotonic()

    while time.monotonic() < deadline:
        count = await _get_server_received_count(host, port)
        if count >= n_expected:
            return count, time.monotonic() - t_start
        await asyncio.sleep(POLL_INTERVAL)

    # Timeout — ne kadar aldıysak o kadar
    count = await _get_server_received_count(host, port)
    return count, time.monotonic() - t_start


async def run_qdap_quic_benchmark(
    host:         str   = "172.20.0.10",
    port:         int   = 19701,
    n_messages:   int   = 200,
    payload_size: int   = 1024,
) -> QDAPQuicMetrics:
    """
    QDAP QUIC Ghost Session benchmark — doğru ölçüm.

    Adımlar:
      1. Stats sayacını sıfırla
      2. QUIC bağlantısı kur
      3. t_start kaydet
      4. n_messages mesajı fire-and-forget gönder
      5. TCP poll: server n_messages aldığında t_end kaydet
      6. duration = t_end - t_start
    """
    config = QuicConfiguration(
        alpn_protocols=["qdap-ghost"],
        is_client=True,
        verify_mode=ssl.CERT_NONE,
    )
    config.load_verify_locations(CERT_PATH)

    payload = b"Q" * payload_size

    # Adım 1: Sayacı sıfırla
    await _reset_server_count(STATS_HOST, STATS_PORT)
    await asyncio.sleep(0.1)

    send_latencies = []

    async with connect(
        host, port,
        configuration=config,
        create_protocol=QDAPQuicGhostProtocol,
    ) as client:
        await client.wait_connected()

        # Adım 3: Timer başlat
        t_start = time.monotonic()

        # Adım 4: Fire-and-forget gönderim
        for _ in range(n_messages):
            t0 = time.monotonic_ns()
            await client.send_ghost(payload)
            send_latencies.append((time.monotonic_ns() - t0) / 1e6)

        # QUIC bağlantısını açık tut — server okurken kapanmasın
        # Adım 5: Server tüm mesajları alana kadar bekle
        actual_received, wait_duration = await _wait_for_all_received(
            n_expected=n_messages,
            host=STATS_HOST,
            port=STATS_PORT,
            timeout=120.0,
        )

    # Adım 6: Gerçek süre = gönderim + ağ geçiş süresi
    total_duration = (time.monotonic() - t_start)

    # Throughput: server'ın aldığı byte'lara göre hesapla
    received_bytes = actual_received * payload_size
    throughput     = (received_bytes / total_duration / (1024*1024) * 8
                      if total_duration > 1e-9 else 0)

    lats_sorted = sorted(send_latencies)
    p99_idx     = max(0, int(len(lats_sorted) * 0.99) - 1)

    return QDAPQuicMetrics(
        n_messages=n_messages,
        n_received=actual_received,
        payload_bytes=received_bytes,
        ack_bytes_sent=0,
        throughput_mbps=throughput,
        p99_latency_ms=lats_sorted[p99_idx] if lats_sorted else 0,
        duration_sec=total_duration,
    )
