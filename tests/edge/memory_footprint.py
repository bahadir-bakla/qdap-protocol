#!/usr/bin/env python3
"""
QDAP Memory Footprint Analysis
================================
IoT cihazlar için kritik: RAM kullanımı.

Ölçülenler:
  - Scheduler memory (θ vektörü + window)
  - Ghost Session state per connection
  - Delta Encoder state per stream
  - Full QDAP stack idle memory
  - Comparison: MQTT library idle memory
"""

import gc
import sys
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

G="\033[92m"; Y="\033[93m"; C="\033[96m"
W="\033[97m"; BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"


def measure_bytes(fn) -> int:
    """Bir fonksiyonun allocate ettiği byte'ı ölç."""
    gc.collect()
    tracemalloc.start()
    result = fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def kb(n: int) -> str:
    return f"{n/1024:.2f} KB"


def measure_scheduler():
    """QFT Scheduler memory."""
    from qdap.scheduler.qft_scheduler import QFTScheduler
    def create():
        s = QFTScheduler()
        for _ in range(100):
            s.decide(1024, 20.0, 0.01)
        return s
    return measure_bytes(create)


def measure_ghost_session(n_connections: int = 1):
    """Ghost Session per connection."""
    from qdap.broker.ghost_session_adaptive import AdaptiveGhostSession
    def create():
        sessions = []
        for i in range(n_connections):
            s = AdaptiveGhostSession(f"device_{i}")
            s.on_data_received()
            sessions.append(s)
        return sessions
    return measure_bytes(create)


def measure_delta_encoder():
    """Delta Encoder state."""
    from qdap.compression.delta_encoder import DeltaEncoder, DeltaDecoder
    def create():
        enc = DeltaEncoder()
        dec = DeltaDecoder()
        for i in range(100):
            data = {"temp": 23.0 + i*0.1, "humidity": 65, "co2": 412}
            frame = enc.encode(data)
            dec.decode(frame)
        return enc, dec
    return measure_bytes(create)


def measure_session_cache():
    """Session Cache memory."""
    try:
        from qdap.scheduler.session_cache import SessionCache
        def create():
            cache = SessionCache()
            from qdap.scheduler.qft_scheduler import QFTScheduler
            for i in range(10):
                s = QFTScheduler()
                for _ in range(50):
                    s.decide(1024, 20.0, 0.01)
                cache.save(f"device_{i}", s)
            return cache
        return measure_bytes(create)
    except ImportError:
        return 0


def measure_full_stack():
    """Tam QDAP stack idle memory."""
    from qdap.scheduler.qft_scheduler import QFTScheduler
    from qdap.broker.ghost_session_adaptive import AdaptiveGhostSession
    from qdap.compression.delta_encoder import DeltaEncoder
    def create():
        components = []
        components.append(QFTScheduler())
        components.append(AdaptiveGhostSession("device_001"))
        components.append(DeltaEncoder())
        return components
    return measure_bytes(create)


def measure_mqtt_library():
    """MQTT library idle memory (karşılaştırma)."""
    try:
        import paho.mqtt.client as mqtt
        def create():
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            return c
        return measure_bytes(create)
    except ImportError:
        return -1


def run_analysis():
    print(f"\n{BOLD}{C}{'═'*55}{RESET}")
    print(f"{BOLD}{W}  QDAP Memory Footprint Analysis{RESET}")
    platform = sys.platform
    arch = "arm64" if ("arm" in sys.platform or "aarch64" in sys.platform) else "x86_64"
    print(f"{DIM}  Platform: {platform} | Arch: {arch} | Python {sys.version.split()[0]}{RESET}")
    print(f"{BOLD}{C}{'═'*55}{RESET}\n")

    results = {}

    tests = [
        ("QFT Scheduler (100 decisions)",    measure_scheduler),
        ("Ghost Session (1 connection)",      lambda: measure_ghost_session(1)),
        ("Ghost Session (100 connections)",   lambda: measure_ghost_session(100)),
        ("Delta Encoder (100 messages)",      measure_delta_encoder),
        ("Session Cache (10 devices)",        measure_session_cache),
        ("Full QDAP Stack (idle)",            measure_full_stack),
        ("MQTT Library (idle, comparison)",   measure_mqtt_library),
    ]

    for name, fn in tests:
        try:
            bytes_used = fn()
            if bytes_used == 0:
                print(f"  {Y}⚠{RESET}  {name:<40} SKIPPED")
                continue
            if bytes_used == -1:
                print(f"  {Y}⚠{RESET}  {name:<40} paho-mqtt not installed")
                continue
            results[name] = bytes_used
            color = G if bytes_used < 512*1024 else Y  # >512KB sarı
            print(f"  {color}●{RESET}  {name:<40} {kb(bytes_used):>12}")
        except Exception as e:
            print(f"  \033[91m✗{RESET}  {name:<40} ERROR: {e}")

    # Summary
    print(f"\n{BOLD}{C}━━ Özet ━━{RESET}")
    full = results.get("Full QDAP Stack (idle)", 0)
    mqtt = results.get("MQTT Library (idle, comparison)", 0)
    if full > 0:
        print(f"  QDAP full stack idle: {kb(full)}")
    if mqtt > 0:
        print(f"  MQTT library idle:    {kb(mqtt)}")
        if full > 0:
            ratio = full / mqtt
            print(f"  Ratio QDAP/MQTT:      {ratio:.2f}×")

    # Raspberry Pi Zero 2W has 512MB RAM
    ghost_100 = results.get("Ghost Session (100 connections)", 0)
    if ghost_100 > 0:
        per_conn = ghost_100 / 100
        max_conns_512mb = int(512 * 1024 * 1024 * 0.5 / per_conn)
        print(f"  Ghost Session per conn: {per_conn/1024:.2f} KB")
        print(f"  Max connections (512MB, 50% RAM): ~{max_conns_512mb:,}")

    import json
    out = Path(__file__).parent.parent.parent / "benchmarks/results/memory_footprint.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "platform":       sys.platform,
            "python_version": sys.version,
            "results_bytes":  {k: v for k, v in results.items()},
            "results_kb":     {k: round(v/1024, 2) for k, v in results.items()},
        }, f, indent=2)
    print(f"\n{G}✅ Kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    run_analysis()
