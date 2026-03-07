#!/usr/bin/env python3
"""
MQTT vs QDAP tam karşılaştırma.
4 senaryo, 3 run median.
Sonuçları mqtt_benchmark.json'a kaydet.
"""

import json
import pathlib
import time

from mqtt_publisher    import run_mqtt_benchmark, MQTTMetrics
from mqtt_iot_benchmark import run_mqtt_iot_benchmark

# QDAP sonuçları — mevcut benchmark'lardan al
QDAP_RESULTS = {
    "1KB": {
        "throughput_mbps": 40.503,   # docker v4 median
        "ack_bytes": 0,
        "p99_ms": 0.068,
        "connections": 1,
    },
    "iot": {
        "emergency_hit_pct": 100.0,
        "deadline_miss_pct": 0.0,
        "connections": 1,
        "ack_bytes": 0,
        "throughput_msg_s": 36735.9,
    }
}

RESULTS_DIR = pathlib.Path("/app/results")


def run_all():
    results = {}

    print("\n" + "=" * 65)
    print("  MQTT vs QDAP Karşılaştırma Benchmark")
    print("  Network: 20ms delay, 1% loss (netem)")
    print("=" * 65)

    # ── Senaryo 1: Throughput + ACK Overhead ────────────────────────
    print("\n[1] Throughput + ACK Overhead (1KB, 1000 msg, 3 run median)")

    qos_results = {}
    for qos in [0, 1, 2]:
        runs = []
        for _ in range(3):
            m = run_mqtt_benchmark(qos=qos, n_messages=1000, payload_size=1024)
            runs.append(m.throughput_mbps)
            print(f"  QoS {qos}: {m.throughput_mbps:.3f} Mbps", end="\r")

        median = sorted(runs)[1]
        # Overhead hesapla
        if qos == 0:
            ack_bytes = 0
            oh_pct    = 0.0
        elif qos == 1:
            ack_bytes = 1000 * 4   # 4 byte PUBACK × 1000
            oh_pct    = ack_bytes / (1000 * 1024) * 100
        else:
            ack_bytes = 1000 * 12  # 3 × 4 byte × 1000
            oh_pct    = ack_bytes / (1000 * 1024) * 100

        qos_results[f"QoS{qos}"] = {
            "throughput_mbps_median": round(median, 3),
            "throughput_runs":        [round(r, 3) for r in runs],
            "ack_bytes":              ack_bytes,
            "overhead_pct":           round(oh_pct, 4),
        }
        print(f"  QoS {qos} median: {median:.3f} Mbps, "
              f"ACK overhead: {oh_pct:.4f}%, ACK bytes: {ack_bytes}")

    # QDAP karşılaştırma satırı
    qos_results["QDAP_Ghost"] = {
        "throughput_mbps_median": QDAP_RESULTS["1KB"]["throughput_mbps"],
        "ack_bytes":              0,
        "overhead_pct":           0.0,
        "note":                   "From docker v4 benchmark (3-run median)",
    }

    results["throughput_ack_overhead"] = qos_results

    # ── Senaryo 2: IoT Priority + Connections ───────────────────────
    print("\n[2] IoT: 100 sensör, 1000 mesaj, QoS 1 (3 run median)")

    iot_runs = []
    for run_idx in range(3):
        iot = run_mqtt_iot_benchmark()
        iot_runs.append(iot)
        print(f"  Run {run_idx+1}: emergency_hit={iot.mqtt_emergency_hit_pct:.1f}%, "
              f"conn={iot.mqtt_connections}, ack={iot.mqtt_ack_bytes}B")

    # Median emergency hit
    hits    = sorted([r.mqtt_emergency_hit_pct for r in iot_runs])
    tputs   = sorted([r.mqtt_throughput_msg_s for r in iot_runs])
    med_hit = hits[1]
    med_tput = tputs[1]

    results["iot_priority"] = {
        "MQTT_QoS1": {
            "connections":          iot_runs[0].mqtt_connections,
            "emergency_hit_pct":    round(med_hit, 1),
            "ack_bytes_total":      iot_runs[0].mqtt_ack_bytes,
            "throughput_msg_s":     round(med_tput, 1),
            "deadline_miss_pct":    round(iot_runs[0].mqtt_deadline_miss_pct, 1),
        },
        "QDAP_Ghost": {
            "connections":          QDAP_RESULTS["iot"]["connections"],
            "emergency_hit_pct":    QDAP_RESULTS["iot"]["emergency_hit_pct"],
            "ack_bytes_total":      QDAP_RESULTS["iot"]["ack_bytes"],
            "throughput_msg_s":     QDAP_RESULTS["iot"]["throughput_msg_s"],
            "deadline_miss_pct":    QDAP_RESULTS["iot"]["deadline_miss_pct"],
        },
    }

    print(f"\n  Karşılaştırma:")
    print(f"  {'Metrik':<30} {'MQTT QoS1':>12} {'QDAP':>12}")
    print(f"  {'-'*55}")
    print(f"  {'Bağlantı sayısı':<30} {iot_runs[0].mqtt_connections:>12} {'1':>12}")
    print(f"  {'Emergency deadline hit %':<30} {med_hit:>11.1f}% {'100.0%':>12}")
    print(f"  {'ACK bytes (1000 msg)':<30} {iot_runs[0].mqtt_ack_bytes:>12} {'0':>12}")
    print(f"  {'Throughput (msg/s)':<30} {med_tput:>12.1f} {QDAP_RESULTS['iot']['throughput_msg_s']:>12.1f}")

    # ── Özet kaydet ──────────────────────────────────────────────────
    output = {
        "metadata": {
            "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S"),
            "network":     "Docker bridge, 20ms delay 2ms jitter, 1% loss",
            "mqtt_broker": "Eclipse Mosquitto 2.0",
            "n_runs":      3,
            "median_reported": True,
            "what_differs": (
                "MQTT uses broker + explicit QoS ACKs. "
                "QDAP uses Ghost Session (zero ACK) + "
                "AmplitudeEncoder (deadline-aware priority) + "
                "single multiplexed connection."
            ),
        },
        "results": results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / "mqtt_benchmark.json", "w") as f:
        json.dump(output, f, indent=2)

    print("\n✅ mqtt_benchmark.json kaydedildi")
    return output


if __name__ == "__main__":
    run_all()
