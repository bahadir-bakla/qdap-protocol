# QDAP — Final Benchmark Plan
## v5 Clean + v5 Secure + WAN Test
## Mac + Windows (Hotspot/WiFi) Kurulum

---

## GENEL PLAN

```
Adım 1: v5 Clean Benchmark    → GhostSession (şifrelemesiz)
         Docker, 20ms delay    → v4'ü confirm et + Rust etkisi
         Beklenen: 1KB ~120×

Adım 2: v5 Secure Benchmark   → SecureGhostSession (şifreli)
         Docker, 20ms delay    → güvenlik overhead'i ölç
         Beklenen: 1KB ~100-110×

Adım 3: WAN Test               → Mac + Windows, gerçek internet
         Mac: WiFi              → sender
         Windows: hotspot       → receiver
         Ölçüm: Ghost Session F1, throughput, latency
```

---

# ═══════════════════════════════════════════
# ADIM 1 — v5 Clean Benchmark Fix
# GhostSession (şifrelemesiz)
# ═══════════════════════════════════════════

## Tek Değişiklik

```
docker_benchmark/sender/qdap_client.py dosyasını aç.

Şu satırı bul:
  from qdap.session.secure_ghost_session import SecureGhostSession

Şununla değiştir:
  from qdap.session.ghost_session import GhostSession

Ve session oluşturma satırını bul:
  session = SecureGhostSession(reader, writer)
  await session.perform_handshake(is_client=True)

Şununla değiştir:
  session = GhostSession(reader, writer)

Başka hiçbir şeye DOKUNMA.
```

## Çalıştır

```bash
cd docker_benchmark
docker compose up --build sender receiver
# Benchmark tamamlandığında:
cat results/adaptive_benchmark_v5_clean.json
```

## Beklenen Sonuçlar

```
1KB:   Classical ~0.33 Mbps  | QDAP ~40 Mbps   | ~120×
64KB:  Classical ~7 Mbps     | QDAP ~9 Mbps    | ~1.3×
1MB:   Classical ~9 Mbps     | QDAP ~9 Mbps    | ~1.0×
10MB:  Classical ~8 Mbps     | QDAP ~8.5 Mbps  | ~1.03×
100MB: Classical ~8 Mbps     | QDAP ~8.2 Mbps  | ~1.01×

Eğer 1KB hâlâ 30× çıkarsa:
  Rust bridge PyO3 overhead var demek
  _rust_bridge.py'de RUST_AVAILABLE = False yap
  Tekrar çalıştır
  Fark görünürse Rust overhead sorunu confirm edilir
  Paper'da "PyO3 overhead small payloads için
  disabled" olarak not düş
```

## JSON Adı

```
docker_benchmark/results/adaptive_benchmark_v5_clean.json
```

---

# ═══════════════════════════════════════════
# ADIM 2 — v5 Secure Benchmark
# SecureGhostSession (X25519 + AES-GCM)
# ═══════════════════════════════════════════

## Değişiklik

```
docker_benchmark/sender/qdap_client.py dosyasında:

FROM:
  from qdap.session.ghost_session import GhostSession
  session = GhostSession(reader, writer)

TO:
  from qdap.session.secure_ghost_session import SecureGhostSession
  session = SecureGhostSession(reader, writer)
  await session.perform_handshake(is_client=True)

docker_benchmark/receiver/qdap_server.py dosyasında da:
  SecureGhostSession kullan
  await session.perform_handshake(is_client=False)
```

## Çalıştır

```bash
docker compose up --build sender receiver
cat results/adaptive_benchmark_v5_secure.json
```

## Beklenen Sonuçlar

```
1KB:   ~100-110× (şifreleme overhead ~10-15%)
64KB:  ~1.2×
1MB:   ~0.95-1.0×

AES-GCM overhead per frame:
  +28 byte (12 nonce + 16 tag)
  1KB payload: 28/1024 = 2.7% overhead
  Throughput etkisi: ~5-10%
```

## JSON Adı

```
docker_benchmark/results/adaptive_benchmark_v5_secure.json
```

---

# ═══════════════════════════════════════════
# ADIM 3 — WAN Test
# Mac (WiFi) ↔ Windows (Hotspot)
# ═══════════════════════════════════════════

## Fiziksel Kurulum

```
Sen:
  Mac → evdeki WiFi'a bağlı → SENDER
  
Telefon:
  Hotspot aç → Windows'a bağla → RECEIVER

Trafik yolu:
  Mac (WiFi router) → İnternet → Telefon Hotspot → Windows
  Bu gerçek WAN — farklı IP subnet, farklı carrier
```

## Ön Kontrol

```bash
# Mac'te çalıştır:
curl ifconfig.me
# Örnek: 85.123.45.67 (senin public IP'n)

# Windows'ta çalıştır (cmd):
curl ifconfig.me  
# Örnek: 95.234.56.78 (hotspot IP'si)

# İkisi farklıysa → gerçek WAN ✅
# Aynıysa → aynı ağdasın, hotspot çalışmamış

# RTT ölç:
# Mac'ten Windows'a ping at
ping <windows_local_ip>
# Örnek: ping 192.168.137.1
# Beklenen: 10-80ms
```

## Windows'ta Receiver Kurulum

```bash
# Windows'ta (PowerShell veya cmd):

# Python 3.11+ yüklü mü?
python --version

# QDAP repo'yu kopyala (veya zip ile transfer et)
git clone <repo_url>
cd quantum-protocol

# Bağımlılıkları yükle
pip install -r requirements.txt

# Firewall — port aç (Windows PowerShell admin olarak):
netsh advfirewall firewall add rule name="QDAP Classical" dir=in action=allow protocol=TCP localport=19600
netsh advfirewall firewall add rule name="QDAP Ghost" dir=in action=allow protocol=TCP localport=19601
netsh advfirewall firewall add rule name="QDAP Secure" dir=in action=allow protocol=TCP localport=19602

# Windows local IP'yi öğren:
ipconfig
# "Hotspot" veya "Wireless LAN" altındaki IPv4 adres
# Örnek: 192.168.137.1
```

## WAN Receiver Script (Windows'ta çalışır)

```python
# wan_benchmark/wan_receiver.py
"""
WAN test receiver.
Windows veya Linux'ta çalışır.
3 server başlatır:
  19600: Classical TCP receiver (ACK gönderir)
  19601: QDAP Ghost Session receiver (ACK göndermez)
  19602: QDAP Secure receiver (X25519 + AES-GCM)
"""

import asyncio
import struct
import time
import json
import pathlib
from collections import defaultdict

# Sayaçlar
stats = defaultdict(lambda: {
    "received": 0,
    "bytes": 0,
    "t_first": None,
    "t_last": None,
})

ACK = struct.pack(">Q", 0xDEADBEEFCAFEBABE)   # 8 byte ACK


# ── Classical Receiver ───────────────────────────────────────────

async def classical_handler(reader, writer):
    """Her mesaja ACK döner."""
    peer = writer.get_extra_info("peername")
    while True:
        try:
            # Length prefix oku
            length_bytes = await asyncio.wait_for(
                reader.readexactly(4), timeout=60.0
            )
            length = struct.unpack(">I", length_bytes)[0]
            if length == 0:
                break

            payload = await asyncio.wait_for(
                reader.readexactly(length), timeout=60.0
            )

            s = stats["classical"]
            s["received"] += 1
            s["bytes"]    += length
            if s["t_first"] is None:
                s["t_first"] = time.monotonic()
            s["t_last"] = time.monotonic()

            # ACK gönder
            writer.write(ACK)
            await writer.drain()

        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break

    writer.close()


# ── Ghost Session Receiver ───────────────────────────────────────

async def ghost_handler(reader, writer):
    """ACK GÖNDERMEZ — Ghost Session."""
    while True:
        try:
            length_bytes = await asyncio.wait_for(
                reader.readexactly(4), timeout=60.0
            )
            length = struct.unpack(">I", length_bytes)[0]
            if length == 0:
                break

            payload = await asyncio.wait_for(
                reader.readexactly(length), timeout=60.0
            )

            s = stats["ghost"]
            s["received"] += 1
            s["bytes"]    += length
            if s["t_first"] is None:
                s["t_first"] = time.monotonic()
            s["t_last"] = time.monotonic()

            # ACK YOK — Ghost Session

        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break

    writer.close()


# ── Secure Ghost Receiver ────────────────────────────────────────

async def secure_handler(reader, writer):
    """X25519 handshake + AES-GCM decrypt. ACK göndermez."""
    try:
        from qdap.security.handshake import perform_server_handshake
        from qdap.security.encrypted_frame import FrameEncryptor

        # Handshake
        session_keys = await perform_server_handshake(reader, writer)
        decryptor    = FrameEncryptor(session_keys.data_key)

        while True:
            try:
                length_bytes = await asyncio.wait_for(
                    reader.readexactly(4), timeout=60.0
                )
                length = struct.unpack(">I", length_bytes)[0]
                if length == 0:
                    break

                wire = await asyncio.wait_for(
                    reader.readexactly(length), timeout=60.0
                )

                result = decryptor.unpack(wire)
                if not result.verified:
                    print("⚠️  Frame authentication failed!")
                    break

                s = stats["secure"]
                s["received"] += 1
                s["bytes"]    += len(result.plaintext)
                if s["t_first"] is None:
                    s["t_first"] = time.monotonic()
                s["t_last"] = time.monotonic()

            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
                break

    except Exception as e:
        print(f"Secure handler error: {e}")

    writer.close()


# ── Stats HTTP Server ─────────────────────────────────────────────

async def stats_handler(reader, writer):
    """GET /stats → mevcut sayaçları döndür."""
    await reader.read(1024)
    body = json.dumps({
        k: {
            "received":   v["received"],
            "bytes":      v["bytes"],
            "duration_s": (v["t_last"] - v["t_first"])
                          if v["t_first"] and v["t_last"] else 0,
        }
        for k, v in stats.items()
    })
    resp = (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n{body}"
    )
    writer.write(resp.encode())
    await writer.drain()
    writer.close()


async def reset_handler(reader, writer):
    """POST /reset → sayaçları sıfırla."""
    await reader.read(1024)
    stats.clear()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()


async def control_handler(reader, writer):
    req = (await reader.read(64)).decode(errors="ignore")
    if "reset" in req.lower():
        await reset_handler(reader, writer)
    else:
        await stats_handler(reader, writer)


async def main():
    servers = await asyncio.gather(
        asyncio.start_server(classical_handler, "0.0.0.0", 19600),
        asyncio.start_server(ghost_handler,     "0.0.0.0", 19601),
        asyncio.start_server(secure_handler,    "0.0.0.0", 19602),
        asyncio.start_server(control_handler,   "0.0.0.0", 19603),
    )
    print("✅ WAN Receiver hazır:")
    print("  19600/TCP — Classical (ACK)")
    print("  19601/TCP — QDAP Ghost Session")
    print("  19602/TCP — QDAP Secure (X25519+AES)")
    print("  19603/TCP — Stats/Control")
    print("\nBu pencereyi açık bırak — Mac'ten bağlantı bekleniyor...")

    async with asyncio.TaskGroup() as tg:
        for s in servers:
            tg.create_task(s.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
```

## WAN Sender Script (Mac'te çalışır)

```python
# wan_benchmark/wan_sender.py
"""
WAN test sender — Mac'te çalışır.
3 protokolü test eder: Classical, Ghost, Secure Ghost
Her biri için: 3 run, median raporla.

Kullanım:
  python wan_sender.py --host <windows_ip> --rtt <ping_ms>
  
Örnek:
  python wan_sender.py --host 192.168.137.1 --rtt 45
"""

import argparse
import asyncio
import json
import struct
import time
import statistics
import pathlib
from dataclasses import dataclass


ACK_SIZE = 8


@dataclass
class WanResult:
    protocol:        str
    host:            str
    rtt_ms:          float
    n_messages:      int
    payload_size:    int
    throughput_mbps: float
    p99_latency_ms:  float
    ack_bytes:       int
    duration_sec:    float


# ── Yardımcı: stats poll ─────────────────────────────────────────

async def get_stats(host: str, port: int = 19603) -> dict:
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        w.write(b"GET /stats HTTP/1.0\r\n\r\n")
        await w.drain()
        resp = await asyncio.wait_for(r.read(4096), timeout=5.0)
        w.close()
        body = resp.decode(errors="ignore").split("\r\n\r\n", 1)
        return json.loads(body[1]) if len(body) > 1 else {}
    except Exception:
        return {}


async def reset_stats(host: str, port: int = 19603) -> None:
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        w.write(b"POST /reset HTTP/1.0\r\n\r\n")
        await w.drain()
        await r.read(256)
        w.close()
    except Exception:
        pass


# ── Classical Sender ─────────────────────────────────────────────

async def run_classical(
    host: str, n: int, size: int
) -> WanResult:
    payload = b"C" * size
    lats    = []
    t_start = time.monotonic()

    r, w = await asyncio.open_connection(host, 19600)

    for _ in range(n):
        t0 = time.monotonic_ns()
        w.write(struct.pack(">I", size) + payload)
        await w.drain()
        await asyncio.wait_for(r.readexactly(ACK_SIZE), timeout=30.0)
        lats.append((time.monotonic_ns() - t0) / 1e6)

    # Bağlantıyı kapat (length=0 sinyali)
    w.write(struct.pack(">I", 0))
    await w.drain()
    w.close()

    duration = time.monotonic() - t_start
    lats_s   = sorted(lats)
    p99      = lats_s[int(len(lats_s) * 0.99)]

    return WanResult(
        protocol="Classical_TCP",
        host=host,
        rtt_ms=0,
        n_messages=n,
        payload_size=size,
        throughput_mbps=(n * size) / duration / (1024*1024) * 8,
        p99_latency_ms=p99,
        ack_bytes=n * ACK_SIZE,
        duration_sec=duration,
    )


# ── Ghost Session Sender ──────────────────────────────────────────

async def run_ghost(
    host: str, n: int, size: int
) -> WanResult:
    payload = b"G" * size
    t_start = time.monotonic()

    r, w = await asyncio.open_connection(host, 19601)

    for _ in range(n):
        w.write(struct.pack(">I", size) + payload)

    await w.drain()

    # Tüm mesajlar server'a ulaşana kadar bekle
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        s = await get_stats(host)
        if s.get("ghost", {}).get("received", 0) >= n:
            break
        await asyncio.sleep(0.1)

    w.write(struct.pack(">I", 0))
    await w.drain()
    w.close()

    duration = time.monotonic() - t_start

    return WanResult(
        protocol="QDAP_Ghost",
        host=host,
        rtt_ms=0,
        n_messages=n,
        payload_size=size,
        throughput_mbps=(n * size) / duration / (1024*1024) * 8,
        p99_latency_ms=0,   # Fire-and-forget — per-message latency yok
        ack_bytes=0,
        duration_sec=duration,
    )


# ── Secure Ghost Sender ───────────────────────────────────────────

async def run_secure(
    host: str, n: int, size: int
) -> WanResult:
    from qdap.security.handshake import perform_client_handshake
    from qdap.security.encrypted_frame import FrameEncryptor

    payload = b"S" * size
    t_start = time.monotonic()

    r, w = await asyncio.open_connection(host, 19602)

    # Handshake
    session_keys = await perform_client_handshake(r, w)
    encryptor    = FrameEncryptor(session_keys.data_key)

    for _ in range(n):
        encrypted = encryptor.pack(payload)
        w.write(struct.pack(">I", len(encrypted)) + encrypted)

    await w.drain()

    # Tüm mesajlar ulaşana kadar bekle
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        s = await get_stats(host)
        if s.get("secure", {}).get("received", 0) >= n:
            break
        await asyncio.sleep(0.1)

    w.write(struct.pack(">I", 0))
    await w.drain()
    w.close()

    duration = time.monotonic() - t_start

    return WanResult(
        protocol="QDAP_Secure",
        host=host,
        rtt_ms=0,
        n_messages=n,
        payload_size=size,
        throughput_mbps=(n * size) / duration / (1024*1024) * 8,
        p99_latency_ms=0,
        ack_bytes=0,
        duration_sec=duration,
    )


# ── Runner ────────────────────────────────────────────────────────

PAYLOAD_CONFIGS = [
    ("1KB",  1024,    200),
    ("64KB", 65536,   50),
    ("1MB",  1048576, 10),
]
N_RUNS = 3


async def run_all(host: str, measured_rtt_ms: float):
    print(f"\n{'='*60}")
    print(f"  WAN Benchmark: Mac → Windows")
    print(f"  Host: {host}  |  RTT: {measured_rtt_ms:.1f}ms")
    print(f"  3 protokol × {len(PAYLOAD_CONFIGS)} boyut × {N_RUNS} run")
    print(f"{'='*60}\n")

    all_results = []

    for label, size, n in PAYLOAD_CONFIGS:
        print(f"[{label}] payload={size}B, n={n}")

        for proto_name, run_fn in [
            ("Classical", lambda: run_classical(host, n, size)),
            ("Ghost",     lambda: run_ghost(host, n, size)),
            ("Secure",    lambda: run_secure(host, n, size)),
        ]:
            runs = []
            for i in range(N_RUNS):
                await reset_stats(host)
                await asyncio.sleep(0.5)
                try:
                    result = await run_fn()
                    runs.append(result.throughput_mbps)
                    print(f"  {proto_name} run {i+1}: {result.throughput_mbps:.3f} Mbps")
                except Exception as e:
                    print(f"  {proto_name} run {i+1}: ERROR {e}")
                    runs.append(0.0)

            median = sorted(runs)[N_RUNS // 2]
            ack    = 0 if "Ghost" in proto_name or "Secure" in proto_name else n * ACK_SIZE

            all_results.append({
                "label":          label,
                "payload_size":   size,
                "protocol":       proto_name,
                "tput_runs":      [round(r, 3) for r in runs],
                "tput_median":    round(median, 3),
                "ack_bytes":      ack,
            })

        print()

    # Karşılaştırma tablosu
    print(f"\n{'Label':<8} {'Classical':>12} {'Ghost':>12} {'Secure':>12} {'Ghost/Cls':>10}")
    print("-" * 60)
    for label in [c[0] for c in PAYLOAD_CONFIGS]:
        row = {r["protocol"]: r["tput_median"]
               for r in all_results if r["label"] == label}
        cls    = row.get("Classical", 0)
        ghost  = row.get("Ghost", 0)
        secure = row.get("Secure", 0)
        ratio  = ghost / max(cls, 0.001)
        print(f"{label:<8} {cls:>10.3f}M {ghost:>10.3f}M {secure:>10.3f}M {ratio:>9.2f}×")

    # JSON kaydet
    output = {
        "metadata": {
            "timestamp":        time.strftime("%Y-%m-%dT%H:%M:%S"),
            "test_type":        "WAN — real internet (Mac WiFi ↔ Windows Hotspot)",
            "sender":           "Mac (WiFi)",
            "receiver":         "Windows (Mobile Hotspot)",
            "measured_rtt_ms":  measured_rtt_ms,
            "n_runs":           N_RUNS,
            "median_reported":  True,
            "note":             (
                "Real WAN test. No artificial delay. "
                "RTT measured via ping before benchmark."
            ),
        },
        "results": all_results,
    }

    out = pathlib.Path("wan_benchmark/results/wan_benchmark.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ wan_benchmark.json kaydedildi")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True,
                        help="Windows IP adresi (örn: 192.168.137.1)")
    parser.add_argument("--rtt",  type=float, default=0,
                        help="ping ile ölçülen RTT ms")
    args = parser.parse_args()

    asyncio.run(run_all(args.host, args.rtt))
```

---

## WAN Test Adımları — Sırayla

### Windows'ta (önce başlat):

```bash
# 1. Hotspot aç (telefon veya Windows mobile hotspot)

# 2. Terminal aç
cd quantum-protocol

# 3. IP'ni öğren
ipconfig
# "Wireless LAN adapter Wi-Fi" altındaki IPv4
# Not et → Mac'e söyleyeceksin

# 4. Receiver'ı başlat
python wan_benchmark/wan_receiver.py
# "WAN Receiver hazır" yazısını gör
# Bu pencereyi KAPATMA
```

### Mac'te:

```bash
# 1. Windows IP'ye ping at
ping <windows_ip>
# Çıkan ms değerini not et (RTT)

# 2. Sender'ı çalıştır
cd quantum-protocol
python wan_benchmark/wan_sender.py \
  --host <windows_ip> \
  --rtt <ping_ms>

# Örnek:
python wan_benchmark/wan_sender.py \
  --host 192.168.137.1 \
  --rtt 45.3
```

---

## Teslim Kriterleri

```
3 JSON dosyası:
  docker_benchmark/results/adaptive_benchmark_v5_clean.json
  docker_benchmark/results/adaptive_benchmark_v5_secure.json
  wan_benchmark/results/wan_benchmark.json

Her birinde kontrol:
  ✅ n_runs = 3, median_reported = true
  ✅ qdap_ack_bytes = 0 (Ghost ve Secure)
  ✅ classical_ack_bytes > 0
  ✅ wan_benchmark: measured_rtt_ms > 0
  ✅ wan_benchmark: Ghost tput > 0, Secure tput > 0
```

---

## Paper'a Yansıması

```
Section 5.1 — TCP Benchmark (v5 clean)
  1KB: ~120×, ACK eliminasyonu net
  
Section 5.2 — Güvenlik Overhead Analizi (v5 secure vs clean)
  Tablo:
    Payload  | Clean   | Secure  | Overhead
    1KB      | 120×    | ~108×   | ~10%
    1MB      | 0.99×   | 0.95×   | ~4%
  "AES-GCM adds only 2.7% wire overhead per frame
   (28 bytes per 1024-byte payload)"

Section 5.3 — WAN Validation
  "Tested over real internet: Mac WiFi ↔ Windows Hotspot"
  RTT: XX ms (gerçek ölçüm)
  Ghost Session delivery: verified
  "No artificial delay — real carrier network"
  Bu bölüm "only simulated" itirazını bitirir
```

---

## DOKUNMA

```
Adım 1 (v5 clean):
  SADECE docker_benchmark/sender/qdap_client.py
  SecureGhostSession → GhostSession

Adım 2 (v5 secure):
  docker_benchmark/sender/qdap_client.py → SecureGhostSession
  docker_benchmark/receiver/qdap_server.py → SecureGhostSession

Adım 3 (WAN):
  wan_benchmark/ klasörü oluştur (yeni)
  Mevcut hiçbir şeye dokunma

224 test her adımda geçmeli.
```
