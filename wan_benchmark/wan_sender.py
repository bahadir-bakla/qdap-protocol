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

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat,
)

# Demo: her çalışmada ephemeral Ed25519 identity key üret
_CLIENT_IDENTITY = Ed25519PrivateKey.generate()
_CLIENT_PUB_BYTES = _CLIENT_IDENTITY.public_key().public_bytes(
    Encoding.Raw, PublicFormat.Raw
)


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
        w.write_eof()
        resp = await asyncio.wait_for(r.read(4096), timeout=5.0)
        w.close()
        body = resp.decode(errors="ignore").split("\r\n\r\n", 1)
        return json.loads(body[1]) if len(body) > 1 else {}
    except Exception as e:
        return {}


async def reset_stats(host: str, port: int = 19603) -> None:
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        w.write(b"POST /reset HTTP/1.0\r\n\r\n")
        await w.drain()
        w.write_eof()
        await asyncio.wait_for(r.read(256), timeout=5.0)
        w.close()
    except Exception as e:
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

    # Key exchange: server pub key al (32 byte) → client pub key gönder
    server_pub_raw = await asyncio.wait_for(r.readexactly(32), timeout=10.0)
    server_pub = Ed25519PublicKey.from_public_bytes(server_pub_raw)
    w.write(_CLIENT_PUB_BYTES)
    await w.drain()

    # eCK-model mutual handshake
    session_keys = await perform_client_handshake(r, w, _CLIENT_IDENTITY, server_pub)
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
    ("1KB",   1024,        1000),   # 1000 mesaj
    ("64KB",  65536,        200),   # 200 mesaj
    ("1MB",   1048576,       50),   # 50 mesaj
    ("10MB",  10485760,       5),   # 5 mesaj
]
N_RUNS = 3


async def _run_proto(proto_name: str, host: str, n: int, size: int):
    """Protocol adına göre doğru run_ fonksiyonunu çağır."""
    if proto_name == "Classical":
        return await run_classical(host, n, size)
    elif proto_name == "Ghost":
        return await run_ghost(host, n, size)
    elif proto_name == "Secure":
        return await run_secure(host, n, size)
    else:
        raise ValueError(f"Unknown protocol: {proto_name}")


async def run_all(host: str, measured_rtt_ms: float,
                  protocols: list = None):
    if protocols is None:
        protocols = ["Classical", "Ghost", "Secure"]

    print(f"\n{'='*60}")
    print(f"  WAN Benchmark: Ireland → Singapore")
    print(f"  Host: {host}  |  RTT: {measured_rtt_ms:.1f}ms")
    print(f"  Protocols: {', '.join(protocols)}")
    print(f"  {len(PAYLOAD_CONFIGS)} payload sizes × {N_RUNS} runs each")
    print(f"{'='*60}\n")

    all_results = []

    for label, size, n in PAYLOAD_CONFIGS:
        print(f"[{label}] payload={size}B, n={n}")

        for proto_name in protocols:
            runs = []
            for i in range(N_RUNS):
                await reset_stats(host)
                await asyncio.sleep(0.5)
                try:
                    result = await asyncio.wait_for(
                        _run_proto(proto_name, host, n, size), timeout=90.0
                    )
                    runs.append(result.throughput_mbps)
                    print(f"  {proto_name} run {i+1}: {result.throughput_mbps:.3f} Mbps")
                except asyncio.TimeoutError:
                    print(f"  {proto_name} run {i+1}: TIMEOUT (>90s)")
                    runs.append(0.0)
                except Exception as e:
                    print(f"  {proto_name} run {i+1}: ERROR {e}")
                    runs.append(0.0)

            median = sorted(runs)[N_RUNS // 2]
            ack    = 0 if proto_name in ("Ghost", "Secure") else n * ACK_SIZE

            all_results.append({
                "label":        label,
                "payload_size": size,
                "protocol":     proto_name,
                "tput_runs":    [round(r, 3) for r in runs],
                "tput_median":  round(median, 3),
                "ack_bytes":    ack,
            })

        print()

    # Karşılaştırma tablosu
    col_protos = protocols
    header = f"{'Label':<8}" + "".join(f" {p:>12}" for p in col_protos) + f"  {'Ghost/Cls':>10}"
    print(f"\n{header}")
    print("-" * len(header))
    for label in [c[0] for c in PAYLOAD_CONFIGS]:
        row = {r["protocol"]: r["tput_median"]
               for r in all_results if r["label"] == label}
        line = f"{label:<8}"
        for p in col_protos:
            line += f" {row.get(p, 0):>10.3f}M"
        cls   = row.get("Classical", 0)
        ghost = row.get("Ghost", 0)
        ratio = ghost / max(cls, 0.001) if cls > 0 else 0
        line += f"  {ratio:>9.2f}×"
        print(line)

    # JSON kaydet
    output = {
        "metadata": {
            "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%S"),
            "test_type":       "Cloud WAN — Ireland (eu-west-1) → Singapore (ap-southeast-1)",
            "sender":          "EC2 t3.micro eu-west-1",
            "receiver":        "EC2 t3.micro ap-southeast-1",
            "measured_rtt_ms": measured_rtt_ms,
            "protocols_tested": protocols,
            "n_runs":          N_RUNS,
            "median_reported": True,
            "note": (
                "Real AWS inter-region WAN test.\n"
                "No artificial delay or packet loss simulation.\n"
                "Validates QDAP over real internet path."
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
                        help="Receiver IP (örn: 18.142.108.197)")
    parser.add_argument("--rtt",  type=float, default=0,
                        help="ping ile ölçülen RTT ms")
    parser.add_argument(
        "--protocols",
        default="Classical,Ghost,Secure",
        help="Çalıştırılacak protokoller (virgülle): Classical,Ghost,Secure",
    )
    args = parser.parse_args()

    proto_list = [p.strip() for p in args.protocols.split(",") if p.strip()]
    asyncio.run(run_all(args.host, args.rtt, proto_list))
