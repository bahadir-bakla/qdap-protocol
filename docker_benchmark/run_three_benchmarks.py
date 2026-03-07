"""
Run All Three Benchmarks
============================

1. QUIC/UDP benchmark → quic_benchmark.json
2. IoT Priority benchmark → iot_benchmark.json
3. Ghost vs Keepalive benchmark → keepalive_benchmark.json
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from docker_benchmark.quic.run_quic_benchmark import run_all as run_quic
from docker_benchmark.iot.run_iot_benchmark import run_all as run_iot
from docker_benchmark.keepalive.ghost_vs_keepalive import run_all as run_keepalive


async def main():
    print("\n" + "█" * 70)
    print("  QDAP — Three New Benchmarks")
    print("  1. QUIC/UDP Transport Agnostic")
    print("  2. IoT Priority (AmplitudeEncoder)")
    print("  3. Ghost Session vs Keepalive")
    print("█" * 70)

    t_start = time.monotonic()

    print("\n\n━━━ BENCHMARK 1/3: QUIC/UDP Transport ━━━")
    await run_quic()

    print("\n\n━━━ BENCHMARK 2/3: IoT Priority ━━━")
    await run_iot()

    print("\n\n━━━ BENCHMARK 3/3: Ghost vs Keepalive ━━━")
    await run_keepalive()

    elapsed = time.monotonic() - t_start
    print(f"\n\n{'█' * 70}")
    print(f"  ✅ All 3 benchmarks complete in {elapsed:.0f}s")
    print(f"  📁 quic_benchmark.json")
    print(f"  📁 iot_benchmark.json")
    print(f"  📁 keepalive_benchmark.json")
    print(f"{'█' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
