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
