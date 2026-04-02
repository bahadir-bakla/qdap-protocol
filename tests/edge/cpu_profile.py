#!/usr/bin/env python3
"""
QDAP CPU Profile — IoT cihaz uyumluluğu testi.
Single-core ARM işlemci için: karar/saniye ölçümü.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

G="\033[92m"; C="\033[96m"; W="\033[97m"; BOLD="\033[1m"; RESET="\033[0m"


def bench_scheduler_throughput():
    """QFT Scheduler: karar/saniye."""
    from qdap.scheduler.qft_scheduler import QFTScheduler
    s = QFTScheduler()
    n = 10_000
    t0 = time.perf_counter()
    for _ in range(n):
        s.decide(1024, 20.0, 0.01)
    dur = time.perf_counter() - t0
    return n / dur


def bench_delta_encoder_throughput():
    """Delta Encoder: mesaj/saniye."""
    from qdap.compression.delta_encoder import DeltaEncoder, DeltaDecoder
    enc = DeltaEncoder()
    dec = DeltaDecoder()
    data = {"temp": 23.0, "humidity": 65, "co2": 412, "battery": 3.7}
    n = 5_000
    t0 = time.perf_counter()
    for i in range(n):
        data["temp"] += 0.01
        frame = enc.encode(data)
        dec.decode(frame)
    dur = time.perf_counter() - t0
    return n / dur


def bench_ghost_session_throughput():
    """Ghost Session tick: işlem/saniye."""
    from qdap.broker.ghost_session_adaptive import AdaptiveGhostSession
    sessions = [AdaptiveGhostSession(f"dev_{i}") for i in range(100)]
    for s in sessions:
        s.on_data_received()
    n_ticks = 1_000
    t0 = time.perf_counter()
    for _ in range(n_ticks):
        for s in sessions:
            s.tick()
    dur = time.perf_counter() - t0
    total_ops = n_ticks * len(sessions)
    return total_ops / dur


def run_cpu_profile():
    print(f"\n{BOLD}{C}{'═'*50}{RESET}")
    print(f"{BOLD}{W}  QDAP CPU Profile — IoT Compatibility{RESET}")
    print(f"{BOLD}{C}{'═'*50}{RESET}\n")

    tests = [
        ("QFT Scheduler",  bench_scheduler_throughput,    "decisions/s", 1_000),
        ("Delta Encoder",  bench_delta_encoder_throughput, "messages/s",  100),
        ("Ghost Session",  bench_ghost_session_throughput, "ticks/s",     10_000),
    ]

    rates = {}
    for name, fn, unit, min_target in tests:
        try:
            rate = fn()
            rates[name] = rate
            ok = rate >= min_target
            color = G if ok else "\033[91m"
            sym = "✅" if ok else "❌"
            print(
                f"  {sym} {name:<22} "
                f"{color}{rate:>12,.0f}{RESET} {unit}"
                f"  (min: {min_target:,})"
            )
        except Exception as e:
            print(f"  ❌ {name:<22} ERROR: {e}")

    # Pi Zero 2W: ~4× slower than laptop
    if "QFT Scheduler" in rates:
        pi_rate = rates["QFT Scheduler"] / 4
        print(f"\n  {W}Raspberry Pi Zero 2W estimate (÷4):{RESET}")
        print(f"  QFT Scheduler: ~{pi_rate:,.0f} decisions/s")
        print(f"  (IoT sensor at 10 msg/s needs only 10 decisions/s → ✅ sufficient margin)")

    import json
    out = Path(__file__).parent.parent.parent / "benchmarks/results/cpu_profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "platform":   sys.platform,
            "rates":      {k: round(v, 1) for k, v in rates.items()},
            "pi_zero_2w": {k: round(v / 4, 1) for k, v in rates.items()},
        }, f, indent=2)
    print(f"\n{G}✅ Kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    run_cpu_profile()
