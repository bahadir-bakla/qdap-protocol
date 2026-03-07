#!/usr/bin/env python3
"""
QUIC ACK baseline client.
Her mesaj için: stream aç → veri gönder → ACK bekle → stream kapat
Bu classical request/response pattern'ı — blocking.
"""

import asyncio
import pathlib
import ssl
import time
import logging
from dataclasses import dataclass
from typing import Optional

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived, QuicEvent

logging.getLogger("aioquic").setLevel(logging.ERROR)

CERT_PATH = pathlib.Path(__file__).parent.parent / "certs" / "cert.pem"


@dataclass
class QuicAckMetrics:
    protocol:        str   = "QUIC_ACK_Baseline"
    n_messages:      int   = 0
    payload_bytes:   int   = 0
    ack_bytes_recv:  int   = 0
    throughput_mbps: float = 0.0
    p99_latency_ms:  float = 0.0
    duration_sec:    float = 0.0


class QuicAckClientProtocol(QuicConnectionProtocol):
    """
    Her stream için ACK bekleyen QUIC client.
    _pending: stream_id → asyncio.Event
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending: dict[int, asyncio.Event] = {}
        self._ack_bytes = 0

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived):
            self._ack_bytes += len(event.data)
            if event.stream_id in self._pending:
                self._pending[event.stream_id].set()

    async def send_and_wait(self, payload: bytes, timeout: float = 30.0) -> float:
        """
        Tek mesaj gönder, ACK bekle.
        Returns: latency (ms)
        Raises: asyncio.TimeoutError eğer ACK gelmezse
        """
        stream_id = self._quic.get_next_available_stream_id()
        done      = asyncio.Event()
        self._pending[stream_id] = done

        t0 = time.monotonic_ns()

        self._quic.send_stream_data(
            stream_id=stream_id,
            data=payload,
            end_stream=True,
        )
        self.transmit()

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        finally:
            self._pending.pop(stream_id, None)

        return (time.monotonic_ns() - t0) / 1_000_000  # ms

    @property
    def total_ack_bytes(self) -> int:
        return self._ack_bytes


async def run_quic_ack_benchmark(
    host:         str   = "172.20.0.10",
    port:         int   = 19700,
    n_messages:   int   = 200,
    payload_size: int   = 1024,
) -> QuicAckMetrics:
    """
    QUIC ACK benchmark ana fonksiyonu.
    Her mesaj gönderilir → ACK beklenir → sonraki mesaj.
    """
    # TLS konfigürasyonu — self-signed cert'e izin ver
    config = QuicConfiguration(
        alpn_protocols=["qdap-ack"],
        is_client=True,
        verify_mode=ssl.CERT_NONE,
    )
    # Kendi sertifikamızı CA olarak ekle
    config.load_verify_locations(CERT_PATH)

    payload    = b"A" * payload_size
    latencies  = []
    t_start    = time.monotonic()

    async with connect(
        host, port,
        configuration=config,
        create_protocol=QuicAckClientProtocol,
    ) as client:
        # Bağlantı kurulmasını bekle
        await client.wait_connected()

        for i in range(n_messages):
            try:
                lat = await client.send_and_wait(payload, timeout=30.0)
                latencies.append(lat)
            except asyncio.TimeoutError:
                print(f"  ⚠️  ACK timeout at message {i}/{n_messages}")
                break

    if not latencies:
        return QuicAckMetrics(n_messages=0)

    duration  = time.monotonic() - t_start
    lats      = sorted(latencies)
    p99       = lats[max(0, int(len(lats) * 0.99) - 1)]
    tput      = (len(latencies) * payload_size) / duration / (1024*1024) * 8

    return QuicAckMetrics(
        n_messages=len(latencies),
        payload_bytes=len(latencies) * payload_size,
        ack_bytes_recv=8 * len(latencies),   # 8 byte per ACK
        throughput_mbps=tput,
        p99_latency_ms=p99,
        duration_sec=duration,
    )
