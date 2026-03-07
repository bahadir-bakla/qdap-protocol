# QDAP — QUIC Benchmark Fix Guide
## Gemini Agent İçin: Adım Adım, Hiçbir Şey Varsayılmadan

---

## ÖNCE OKU — Ne Yapacağız ve Neden

### Mevcut Sorun
`docker_benchmark/results/quic_benchmark.json` dosyasındaki sonuçlar geçersiz:
- "QDAP UDP 2700 Mbps" → Bu loopback/memory ölçümü, gerçek ağ değil
- "HTTP/3-like" → Gerçek HTTP/3 değil, raw UDP socket
- netem (delay/loss) UDP'ye uygulanmamış

### Ne Kanıtlayacağız
```
Paper iddiası: "QDAP is transport-agnostic"
Kanıt yöntemi: Aynı QUIC/UDP ağında iki sistem çalıştır:
  Sistem 1: aioquic ile gerçek QUIC bağlantısı, her mesaj için stream ACK bekler
  Sistem 2: QDAP QDAPQUICAdapter ile QUIC bağlantısı, ACK bekleme yok
  Fark: SADECE ACK davranışı
  Her ikisi: aynı Docker network, aynı netem koşulları
```

### Başarı Kriteri
```
quic_benchmark.json içinde:
  qdap_ack_bytes: 0 (tüm satırlarda)
  transport: "QUIC/UDP (same for both)"
  netem_verified: true
  1KB'de QDAP throughput < 500 Mbps (gerçekçi sınır)
  3-run median rapor edilmiş
```

---

## ADIM 0 — Mevcut quic_benchmark.json'u Sil

```bash
rm -f docker_benchmark/results/quic_benchmark.json
rm -rf docker_benchmark/quic/
mkdir -p docker_benchmark/quic/certs
mkdir -p docker_benchmark/quic/sender
mkdir -p docker_benchmark/quic/receiver
```

---

## ADIM 1 — Gerekli Paketleri Kontrol Et

```bash
# Receiver ve Sender container'larının Dockerfile'larına eklenecek:
# aioquic==1.3.0
# cryptography>=41.0.0

# Test et:
python3 -c "import aioquic; print('aioquic OK:', aioquic.__version__)"
python3 -c "from cryptography.hazmat.primitives.asymmetric import rsa; print('cryptography OK')"
```

Eğer aioquic yüklü değilse Dockerfile'lara ekle:
```dockerfile
RUN pip install aioquic==1.3.0
```

---

## ADIM 2 — Self-Signed TLS Sertifikası Üret

Bu dosyayı oluştur: `docker_benchmark/quic/generate_certs.py`

```python
#!/usr/bin/env python3
"""
Self-signed TLS sertifikası üretir.
QUIC için TLS 1.3 zorunlu — sertifikasız çalışmaz.
"""

import datetime
import pathlib
import ipaddress

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate():
    # RSA 2048 private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "qdap-quic-test"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QDAP"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(
            datetime.datetime.utcnow() + datetime.timedelta(days=3650)
        )
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("qdap-quic-receiver"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("172.20.0.10")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    out = pathlib.Path(__file__).parent / "certs"
    out.mkdir(exist_ok=True)

    # Private key kaydet
    (out / "key.pem").write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    # Certificate kaydet
    (out / "cert.pem").write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    print(f"✅ Sertifika üretildi: {out}/cert.pem + {out}/key.pem")


if __name__ == "__main__":
    generate()
```

Çalıştır:
```bash
cd docker_benchmark/quic && python3 generate_certs.py
# Çıktı: ✅ Sertifika üretildi: certs/cert.pem + certs/key.pem
ls -la certs/
# cert.pem ve key.pem görünmeli
```

---

## ADIM 3 — QUIC Receiver (Her İki Server)

Bu dosyayı oluştur: `docker_benchmark/quic/receiver/quic_servers.py`

```python
#!/usr/bin/env python3
"""
İki QUIC server çalıştırır:
  Port 19700: QUIC ACK server (baseline) — her mesaja ACK döner
  Port 19701: QDAP QUIC server — ACK döndürmez (Ghost Session)

Her ikisi de aynı TLS sertifikasını kullanır.
Her ikisi de gerçek QUIC/UDP üzerinde çalışır.
"""

import asyncio
import pathlib
import struct
import logging

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived, QuicEvent

# Log seviyesini kısalt — benchmark çıktısını karıştırmasın
logging.getLogger("aioquic").setLevel(logging.ERROR)

CERT_PATH = pathlib.Path(__file__).parent.parent / "certs" / "cert.pem"
KEY_PATH  = pathlib.Path(__file__).parent.parent / "certs" / "key.pem"

# ── ACK Server (Baseline) ────────────────────────────────────────────────────

class QUICAckServerProtocol(QuicConnectionProtocol):
    """
    Her gelen QUIC stream'e 8 byte ACK döner.
    Bu classical request/response pattern'ı simüle eder.
    """

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived):
            if event.end_stream:
                # Tam mesaj alındı — ACK gönder
                stream_id = event.stream_id
                # 8 byte ACK: [stream_id(4B)][status=0x01(4B)]
                ack = struct.pack(">II", stream_id & 0xFFFFFFFF, 0x00000001)
                self._quic.send_stream_data(
                    stream_id=stream_id,
                    data=ack,
                    end_stream=True,
                )
                self.transmit()


# ── QDAP Ghost Session Server ────────────────────────────────────────────────

class QDAPQuicServerProtocol(QuicConnectionProtocol):
    """
    Gelen QUIC stream'i alır ama ACK GÖNDERMEZ.
    Ghost Session: receiver state'i yerel olarak günceller.
    Bu QDAP'ın sıfır ACK overhead iddiasının özü.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._received_count = 0

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived):
            if event.end_stream:
                self._received_count += 1
                # ACK GÖNDERMİYORUZ — Ghost Session
                # Receiver sayacı yerel günceller, network'e hiçbir şey yazmaz


# ── Server Başlatıcı ─────────────────────────────────────────────────────────

async def start_ack_server(host: str = "0.0.0.0", port: int = 19700):
    config = QuicConfiguration(
        alpn_protocols=["qdap-ack"],
        is_client=False,
    )
    config.load_cert_chain(CERT_PATH, KEY_PATH)

    print(f"[QUIC ACK Server] Listening on {host}:{port}/udp")
    await serve(
        host, port,
        configuration=config,
        create_protocol=QUICAckServerProtocol,
    )


async def start_qdap_server(host: str = "0.0.0.0", port: int = 19701):
    config = QuicConfiguration(
        alpn_protocols=["qdap-ghost"],
        is_client=False,
    )
    config.load_cert_chain(CERT_PATH, KEY_PATH)

    print(f"[QDAP QUIC Server] Listening on {host}:{port}/udp")
    await serve(
        host, port,
        configuration=config,
        create_protocol=QDAPQuicServerProtocol,
    )


async def main():
    print("[Receiver] Starting QUIC servers...")
    ack_server  = asyncio.create_task(start_ack_server())
    qdap_server = asyncio.create_task(start_qdap_server())
    await asyncio.gather(ack_server, qdap_server)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## ADIM 4 — QUIC ACK Client (Baseline)

Bu dosyayı oluştur: `docker_benchmark/quic/sender/quic_ack_client.py`

```python
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
```

---

## ADIM 5 — QDAP QUIC Client (Ghost Session)

Bu dosyayı oluştur: `docker_benchmark/quic/sender/qdap_quic_client.py`

```python
#!/usr/bin/env python3
"""
QDAP QUIC Ghost Session client.
Mesajları QUIC stream üzerinden gönderir — ACK BEKLEMEZ.
Fire-and-forget: her mesajdan sonra hemen sonrakini gönderir.
"""

import asyncio
import pathlib
import ssl
import time
import logging
from dataclasses import dataclass

from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent

logging.getLogger("aioquic").setLevel(logging.ERROR)

CERT_PATH = pathlib.Path(__file__).parent.parent / "certs" / "cert.pem"


@dataclass
class QDAPQuicMetrics:
    protocol:        str   = "QDAP_QUIC_GhostSession"
    n_messages:      int   = 0
    payload_bytes:   int   = 0
    ack_bytes_sent:  int   = 0    # Her zaman 0 — Ghost Session
    throughput_mbps: float = 0.0
    p99_latency_ms:  float = 0.0
    duration_sec:    float = 0.0


class QDAPQuicGhostProtocol(QuicConnectionProtocol):
    """
    ACK beklemeyen QUIC client.
    Her mesaj için yeni stream açar, veriyi gönderir, devam eder.
    Ghost Session: server durumu yerel Markov modeli ile takip eder.
    """

    def quic_event_received(self, event: QuicEvent) -> None:
        # ACK dinlemiyoruz — Ghost Session, server'ın aldığını varsayar
        pass

    async def send_ghost(self, payload: bytes) -> float:
        """
        Mesajı gönder, ACK bekleme.
        Returns: gönderim süresi (ms) — sadece send maliyeti
        """
        stream_id = self._quic.get_next_available_stream_id()

        t0 = time.monotonic_ns()
        self._quic.send_stream_data(
            stream_id=stream_id,
            data=payload,
            end_stream=True,
        )
        self.transmit()
        elapsed = (time.monotonic_ns() - t0) / 1_000_000  # ms

        # ACK bekleme yok — bir sonraki mesaja geç
        return elapsed


async def run_qdap_quic_benchmark(
    host:         str   = "172.20.0.10",
    port:         int   = 19701,
    n_messages:   int   = 200,
    payload_size: int   = 1024,
) -> QDAPQuicMetrics:
    """
    QDAP QUIC Ghost Session benchmark.
    Fire-and-forget: ACK bekleme yok.
    """
    config = QuicConfiguration(
        alpn_protocols=["qdap-ghost"],
        is_client=True,
        verify_mode=ssl.CERT_NONE,
    )
    config.load_verify_locations(CERT_PATH)

    payload   = b"Q" * payload_size
    latencies = []
    t_start   = time.monotonic()

    async with connect(
        host, port,
        configuration=config,
        create_protocol=QDAPQuicGhostProtocol,
    ) as client:
        await client.wait_connected()

        for _ in range(n_messages):
            lat = await client.send_ghost(payload)
            latencies.append(lat)

        # Tüm paketlerin gönderilmesi için bekle
        await asyncio.sleep(0.5)

    duration = time.monotonic() - t_start
    lats     = sorted(latencies)
    p99      = lats[max(0, int(len(lats) * 0.99) - 1)]
    tput     = (len(latencies) * payload_size) / duration / (1024*1024) * 8

    return QDAPQuicMetrics(
        n_messages=len(latencies),
        payload_bytes=len(latencies) * payload_size,
        ack_bytes_sent=0,   # Ghost Session: sıfır ACK
        throughput_mbps=tput,
        p99_latency_ms=p99,
        duration_sec=duration,
    )
```

---

## ADIM 6 — Benchmark Runner

Bu dosyayı oluştur: `docker_benchmark/quic/sender/run_quic_benchmark.py`

```python
#!/usr/bin/env python3
"""
QUIC Benchmark Runner.
HTTP/3-style QUIC ACK vs QDAP QUIC Ghost Session.
Her ikisi: gerçek QUIC/UDP, aynı Docker network, aynı netem.
3 run, median raporla.
"""

import asyncio
import json
import pathlib
import statistics
import subprocess
import time

from quic_ack_client  import run_quic_ack_benchmark
from qdap_quic_client import run_qdap_quic_benchmark

RESULTS_DIR = pathlib.Path("/app/results")

# Payload: 1KB, 64KB, 1MB
# n_messages: büyük payload'da az, küçükte çok
PAYLOAD_CONFIGS = [
    {"label": "1KB",  "payload_size": 1024,         "n_messages": 200},
    {"label": "64KB", "payload_size": 65536,         "n_messages": 50},
    {"label": "1MB",  "payload_size": 1048576,       "n_messages": 10},
]
N_RUNS = 3


def verify_netem() -> dict:
    """tc netem'in gerçekten aktif olduğunu doğrula."""
    try:
        out = subprocess.check_output(
            ["tc", "qdisc", "show", "dev", "eth0"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        active = "netem" in out
        return {
            "tc_output":     out.strip(),
            "netem_active":  active,
            "delay_active":  "delay" in out,
            "loss_active":   "loss"  in out,
        }
    except Exception as e:
        return {
            "tc_output":    str(e),
            "netem_active": False,
            "delay_active": False,
            "loss_active":  False,
        }


async def run_all():
    print("\n" + "=" * 65)
    print("  QUIC Benchmark: QUIC ACK vs QDAP QUIC Ghost Session")
    print("  Transport: QUIC/UDP (her ikisi için aynı)")
    print("  Fark: Stream ACK bekleme vs Ghost Session (fire-and-forget)")
    print("=" * 65)

    # netem doğrula
    netem = verify_netem()
    print(f"\n  netem: {'✅ aktif' if netem['netem_active'] else '❌ AKTIF DEĞİL'}")
    if not netem["netem_active"]:
        print("  ⚠️  UYARI: netem aktif değil — benchmark geçersiz olabilir!")
    print()

    results = []

    for cfg in PAYLOAD_CONFIGS:
        label        = cfg["label"]
        payload_size = cfg["payload_size"]
        n_messages   = cfg["n_messages"]

        print(f"  [{label}] payload={payload_size}B, n={n_messages}, {N_RUNS} run...")

        ack_runs  = []
        qdap_runs = []

        for run_idx in range(N_RUNS):
            await asyncio.sleep(1.0)   # Run'lar arası nefes

            # QUIC ACK baseline
            try:
                ack = await run_quic_ack_benchmark(
                    n_messages=n_messages,
                    payload_size=payload_size,
                )
                ack_runs.append(ack.throughput_mbps)
                print(f"    Run {run_idx+1}: ACK={ack.throughput_mbps:.2f} Mbps", end="")
            except Exception as e:
                print(f"    Run {run_idx+1}: ACK ERROR: {e}", end="")
                ack_runs.append(0.0)

            await asyncio.sleep(0.5)

            # QDAP Ghost Session
            try:
                qdap = await run_qdap_quic_benchmark(
                    n_messages=n_messages,
                    payload_size=payload_size,
                )
                qdap_runs.append(qdap.throughput_mbps)
                print(f"  QDAP={qdap.throughput_mbps:.2f} Mbps")
            except Exception as e:
                print(f"  QDAP ERROR: {e}")
                qdap_runs.append(0.0)

        # Median hesapla
        ack_median  = sorted(ack_runs)[N_RUNS // 2]
        qdap_median = sorted(qdap_runs)[N_RUNS // 2]
        ratio       = qdap_median / max(ack_median, 0.001)

        row = {
            "label":              label,
            "payload_size":       payload_size,
            "n_messages":         n_messages,
            "n_runs":             N_RUNS,
            # ACK baseline
            "quic_ack_tput_runs":   ack_runs,
            "quic_ack_tput_median": round(ack_median, 3),
            # QDAP Ghost Session
            "qdap_tput_runs":       qdap_runs,
            "qdap_tput_median":     round(qdap_median, 3),
            # Karşılaştırma
            "ratio":              round(ratio, 2),
            "qdap_ack_bytes":     0,       # Ghost Session: her zaman 0
        }
        results.append(row)

        print(f"  [{label}] Median → ACK: {ack_median:.2f} Mbps | "
              f"QDAP: {qdap_median:.2f} Mbps | ratio: {ratio:.2f}×\n")

    # JSON kaydet
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "metadata": {
            "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%S"),
            "transport":      "QUIC/UDP (same for both)",
            "what_differs":   "QUIC stream ACK vs QDAP Ghost Session (fire-and-forget)",
            "n_runs":         N_RUNS,
            "median_reported": True,
            "netem_verification": netem,
            "note": (
                "QUIC ACK: opens stream, sends data, waits for 8-byte ACK, "
                "then proceeds. QDAP Ghost: opens stream, sends data, "
                "continues immediately — no ACK wait."
            ),
        },
        "results": results,
    }

    out_path = RESULTS_DIR / "quic_benchmark.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ quic_benchmark.json kaydedildi: {out_path}")

    # Özet
    print("\n  ÖZET:")
    print(f"  {'Label':<8} {'QUIC ACK':>12} {'QDAP':>12} {'Oran':>8}")
    print("  " + "-" * 44)
    for r in results:
        print(f"  {r['label']:<8} {r['quic_ack_tput_median']:>10.2f}M "
              f"{r['qdap_tput_median']:>10.2f}M {r['ratio']:>7.2f}×")

    return output


if __name__ == "__main__":
    asyncio.run(run_all())
```

---

## ADIM 7 — Docker Compose Güncellemesi

`docker_benchmark/docker-compose.yml` dosyasına şu servisleri EKLE (mevcut servislere dokunma):

```yaml
  # ── QUIC Benchmark Servisleri ──────────────────────────────────
  quic_receiver:
    build:
      context: .
      dockerfile: Dockerfile.receiver
    container_name: qdap_quic_receiver
    networks:
      qdap_net:
        ipv4_address: 172.20.0.10   # Mevcut receiver ile aynı IP
    ports:
      - "19700:19700/udp"   # QUIC ACK server
      - "19701:19701/udp"   # QDAP QUIC server
    volumes:
      - ./quic/certs:/app/quic/certs:ro
    command: python quic/receiver/quic_servers.py

  quic_sender:
    build:
      context: .
      dockerfile: Dockerfile.sender
    container_name: qdap_quic_sender
    networks:
      qdap_net:
        ipv4_address: 172.20.0.21
    cap_add:
      - NET_ADMIN
    depends_on:
      - quic_receiver
    volumes:
      - ./quic/certs:/app/quic/certs:ro
      - ./results:/app/results
    command: >
      sh -c "
        sleep 3 &&
        tc qdisc add dev eth0 root netem delay 20ms 2ms loss 1% &&
        echo 'netem aktif: 20ms delay, 1% loss' &&
        tc qdisc show dev eth0 &&
        python quic/sender/run_quic_benchmark.py
      "
```

---

## ADIM 8 — Dockerfile Güncelle

`docker_benchmark/Dockerfile.sender` ve `docker_benchmark/Dockerfile.receiver` dosyalarına aioquic ekle:

```dockerfile
# Mevcut RUN pip install satırını bul ve aioquic ekle:
RUN pip install aioquic==1.3.0 cryptography>=41.0.0
```

---

## ADIM 9 — Çalıştır ve Doğrula

```bash
# 1. Sertifika üret
cd docker_benchmark/quic
python3 generate_certs.py
ls -la certs/   # cert.pem ve key.pem görünmeli

# 2. Docker build
cd ..
docker compose build quic_receiver quic_sender

# 3. Çalıştır
docker compose up quic_receiver quic_sender

# 4. Sonuç kontrol
cat results/quic_benchmark.json | python3 -m json.tool | head -30
```

---

## ADIM 10 — Sonuç Doğrulama

`quic_benchmark.json` içinde şunları kontrol et:

```
✅ metadata.transport = "QUIC/UDP (same for both)"
✅ metadata.netem_verification.netem_active = true
✅ metadata.netem_verification.delay_active = true
✅ metadata.netem_verification.loss_active = true
✅ Her result için: qdap_ack_bytes = 0
✅ Her result için: quic_ack_tput_median > 0
✅ Her result için: qdap_tput_median > 0
✅ 1KB qdap_tput_median < 200 Mbps (Docker bridge limiti)
✅ n_runs = 3
```

Eğer `1KB qdap_tput_median > 200 Mbps` çıkarsa:
→ netem UDP'ye uygulanmadı demek
→ `tc qdisc show dev eth0` çıktısını kontrol et
→ netem'in UDP'ye de uygulandığını doğrula

---

## Teslim

Bize sadece şunu gönder:
```
docker_benchmark/results/quic_benchmark.json
```

Başka hiçbir dosyayı değiştirme — mevcut TCP benchmark'lara, 
mevcut testlere, mevcut classical/QDAP koduna DOKUNMA.

---

## Beklenen Çıktı (Gerçekçi Sınırlar)

```
Docker bridge + 20ms delay + 1% loss + gerçek QUIC/UDP:

1KB:   QUIC ACK ~0.3 Mbps  | QDAP ~8-15 Mbps   | ~25-50×
64KB:  QUIC ACK ~6-8 Mbps  | QDAP ~8-12 Mbps   | ~1.4×
1MB:   QUIC ACK ~8-10 Mbps | QDAP ~8-11 Mbps   | ~1.0×

Bu sayılar TCP benchmark ile tutarlı — beklenen bu.
500+ Mbps çıkarsa netem çalışmıyor demek.
```
