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
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from quic_ack_client  import run_quic_ack_benchmark
from qdap_quic_client import run_qdap_quic_benchmark, _reset_server_count, _get_server_received_count, STATS_HOST, STATS_PORT

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


async def reset_and_verify(host, port):
    # Sıfır olana kadar (10 kereden fazla denerse hata fırlatır) tekrar tekrar sıfırlama yolla
    for _ in range(10):
        await _reset_server_count(host, port)
        await asyncio.sleep(0.2)  # reset'in işlenmesi için bekle
        
        count = await _get_server_received_count(host, port)
        if count == 0:
            return True
            
        await asyncio.sleep(0.5)
        
    raise RuntimeError("Stats counter reset failed!")


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
            await asyncio.sleep(3.0)   # Ağın tamamen durulmasını bekle (Run'lar arası nefes)

            # Her run başında çağır:
            await reset_and_verify(STATS_HOST, STATS_PORT)

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
            "qdap_n_received":      qdap.n_received,   # YENİ: kaç paket ulaştı
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
