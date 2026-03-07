"""
IoT Priority Benchmark Runner
==================================

100 emergency + 300 routine + 600 telemetry = 1000 messages
Classical FIFO vs QDAP AmplitudeEncoder priority
3 runs → iot_benchmark.json
"""

import asyncio
import json
import time
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from docker_benchmark.iot.classical_iot_client import run_classical_iot_benchmark
from docker_benchmark.iot.qdap_iot_client import run_qdap_iot_benchmark


async def run_all():
    print("\n" + "=" * 70)
    print("  IoT Priority Benchmark")
    print("  100 emergency(2ms) + 300 routine(500ms) + 600 telemetry(5s)")
    print("  Classical FIFO vs QDAP AmplitudeEncoder deadline-aware")
    print("=" * 70)

    results = []
    for run_i in range(3):
        await asyncio.sleep(1.0)
        print(f"\n  Run {run_i + 1}/3:")

        print("    Classical FIFO...", end="", flush=True)
        classical = await run_classical_iot_benchmark()
        print(f" ✅ emergency deadline: {classical.emergency_deadline_hit_pct}%, "
              f"conn={classical.connections}")

        await asyncio.sleep(0.5)

        print("    QDAP Priority...", end="", flush=True)
        qdap = await run_qdap_iot_benchmark()
        print(f" ✅ emergency deadline: {qdap.emergency_deadline_hit_pct}%, "
              f"conn={qdap.connections}")

        results.append({
            "run": run_i + 1,
            "classical_emergency_deadline_pct": classical.emergency_deadline_hit_pct,
            "classical_deadline_miss_pct": classical.overall_deadline_miss_pct,
            "classical_connections": classical.connections,
            "classical_tput_msg_s": classical.throughput_msgs_per_s,
            "qdap_emergency_deadline_pct": qdap.emergency_deadline_hit_pct,
            "qdap_deadline_miss_pct": qdap.overall_deadline_miss_pct,
            "qdap_connections": qdap.connections,
            "qdap_ack_bytes": qdap.ack_bytes,
            "qdap_tput_msg_s": qdap.throughput_msgs_per_s,
        })

    output = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scenario": "Mixed IoT: 10% emergency + 30% routine + 60% telemetry",
            "emergency_deadline_ms": 2.0,
            "routine_deadline_ms": 500.0,
            "telemetry_deadline_ms": 5000.0,
            "what_differs": "FIFO vs AmplitudeEncoder priority + single connection",
        },
        "results": results,
    }

    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "iot_benchmark.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  ✅ Saved: {output_path}")
    return output


if __name__ == "__main__":
    asyncio.run(run_all())
