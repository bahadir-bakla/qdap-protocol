# QDAP QUIC — Ölçüm Hatası Debug ve Fix
## Gemini Agent İçin: Tam Diagnoz + Tek Doğru Çözüm

---

## MEVCUT SORUNLARIN TAM TANIMI

### Sorun 1: 64KB'de QDAP < ACK (0.55×)
```
Ne oluyor:
  Client 50 mesaj gönder → STATS_REQUEST gönder
  Server 50 mesajı okurken aynı zamanda STATS cevabı bekliyor
  Bu deadlock benzeri bir durum yaratıyor
  QDAP yavaş görünüyor — gerçek performans değil

Kanıt: TCP benchmark aynı koşulda 64KB QDAP 10 Mbps çıkıyor
       QUIC'te 1.18 Mbps çıkması → ölçüm hatası
```

### Sorun 2: 1MB hâlâ 27 Mbps (beklenen 8-10 Mbps)
```
Ne oluyor:
  STATS_REQUEST son paketten önce server'a ulaşıyor
  Server "hepsini aldım" diyor ama aslında almadı
  Timer erken duruyor → throughput yüksek çıkıyor

Kanıt: TCP benchmark 1MB = 8.55 Mbps
       QUIC 1MB = 27 Mbps → 3× fazla → ölçüm hatası
```

### Kök Neden
```
STATS_REQUEST mekanizması kırık çünkü:
  - QUIC stream sıralaması garanti değil
  - STATS paketi veri paketlerini geçebiliyor
  - Server'da aynı connection'da iki farklı mesaj tipi
    aynı anda işlenemiyor
```

---

## TEK DOĞRU ÇÖZÜM: Ayrı TCP Kontrol Kanalı

```
Fikir:
  QUIC data path → sadece veri, ACK YOK (Ghost Session)
  TCP kontrol kanalı → sadece "kaç aldın?" sorusu (benchmark sync)

Bu şekilde:
  ✅ QUIC data path tamamen temiz (fire-and-forget)
  ✅ Ölçüm doğru (server'ın son paketi aldığı an)
  ✅ Paper iddiası korunuyor ("no application ACK on data path")
  ✅ Deadlock yok
  ✅ Sıralama sorunu yok
```

### Mimari

```
Sender                              Receiver
  │                                    │
  │──── QUIC/UDP port 19701 ──────────▶│  veri akışı (ACK yok)
  │                                    │  server sayar: received_count++
  │                                    │
  │──── TCP port 19702 ───────────────▶│  kontrol kanalı
  │     "GET /stats"                   │
  │◀──── {"received": 47} ─────────────│
  │     (received < n_messages → bekle)│
  │◀──── {"received": 50} ─────────────│  ← TIMER BURADA DURUR
  │                                    │
```

---

## IMPLEMENT EDİLECEK DOSYALAR

### Sadece şu iki dosyayı değiştir:
```
docker_benchmark/quic/receiver/quic_servers.py  ← TCP stats endpoint ekle
docker_benchmark/quic/sender/qdap_quic_client.py ← TCP polling ile ölç
```

---

## YENİ receiver/quic_servers.py

```python
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

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived) and event.end_stream:
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
        if isinstance(event, StreamDataReceived) and event.end_stream:
            _qdap_received_count += 1
            # ACK YOK — Ghost Session


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
```

---

## YENİ sender/qdap_quic_client.py

```python
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
```

---

## Docker Compose Güncellemesi

`docker-compose.yml`'de quic_receiver servisine port 19702 ekle:

```yaml
  quic_receiver:
    # ... mevcut ayarlar ...
    ports:
      - "19700:19700/udp"   # QUIC ACK
      - "19701:19701/udp"   # QDAP QUIC
      - "19702:19702/tcp"   # Stats (YENİ)
```

---

## Benchmark Runner Güncellemesi

`run_quic_benchmark.py`'de result row'a `n_received` ekle:

```python
row = {
    "label":              label,
    "payload_size":       payload_size,
    "n_messages":         n_messages,
    "n_runs":             N_RUNS,
    "quic_ack_tput_runs":   ack_runs,
    "quic_ack_tput_median": round(ack_median, 3),
    "qdap_tput_runs":       qdap_runs,
    "qdap_tput_median":     round(qdap_median, 3),
    "qdap_n_received":      qdap.n_received,   # YENİ: kaç paket ulaştı
    "ratio":              round(ratio, 2),
    "qdap_ack_bytes":     0,
}
```

---

## Doğrulama Kriterleri

Benchmark bittikten sonra kontrol et:

```
✅ netem_active = true
✅ Her satırda qdap_ack_bytes = 0
✅ Her satırda qdap_n_received == n_messages (kayıp yoksa)
   Eğer n_received < n_messages → 1% loss nedeniyle normal

THROUGHPUT KONTROL (TCP benchmark ile tutarlı olmalı):
✅ 1KB  QDAP median: 1-15 Mbps   (önceki: 2.09 ✅)
✅ 64KB QDAP median: 5-15 Mbps   (önceki: 1.18 ❌ → düzelmeli)
✅ 1MB  QDAP median: 5-15 Mbps   (önceki: 27 ❌ → düzelmeli)

EĞER hâlâ 1MB > 20 Mbps çıkarsa:
→ _wait_for_all_received çalışmıyor demek
→ STATS_HOST = "172.20.0.10" doğru mu kontrol et
→ Port 19702 açık mı kontrol et:
   docker exec qdap_quic_receiver nc -zv 0.0.0.0 19702
```

---

## DOKUNMA

```
Şu dosyalara KESİNLİKLE DOKUNMA:
  - docker_benchmark/sender/classical_client.py
  - docker_benchmark/sender/qdap_client.py
  - src/qdap/ altındaki her şey
  - tests/ altındaki her şey
  - docker_benchmark/results/ altındaki mevcut JSON'lar
    (quic_benchmark.json hariç — onu yenile)

Sadece şunları değiştir:
  - docker_benchmark/quic/receiver/quic_servers.py
  - docker_benchmark/quic/sender/qdap_quic_client.py
  - docker_benchmark/quic/sender/run_quic_benchmark.py (n_received ekle)
  - docker-compose.yml (port 19702 ekle)
```

---

## Beklenen Çıktı

```
[1KB]   ACK ~0.3 Mbps  | QDAP ~2-8 Mbps   | ~5-25×
[64KB]  ACK ~2 Mbps    | QDAP ~6-12 Mbps  | ~3-6×
[1MB]   ACK ~2 Mbps    | QDAP ~6-12 Mbps  | ~3-6×

TCP benchmark referans (aynı network koşulları):
  1KB:  8.55 Mbps
  64KB: 10.02 Mbps
  1MB:  8.55 Mbps
QUIC QDAP bu sayılarla ±50% tutarlı olmalı.
```
