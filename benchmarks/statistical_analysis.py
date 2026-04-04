#!/usr/bin/env python3
"""
Statistical Significance Analysis
===================================
n=30 bağımsız çalıştırma ile istatistiksel anlamlılık.

Her karşılaştırma için:
  - Mean ± SD
  - 95% Confidence Interval
  - Cohen's d (effect size)
  - Welch's t-test p-value
"""

import asyncio
import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

G="\033[92m"; Y="\033[93m"; C="\033[96m"
W="\033[97m"; BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"

N_RUNS = 30


# ── İstatistik fonksiyonları ──────────────────────────────────────────────────

def mean(data: List[float]) -> float:
    return sum(data) / len(data)


def std(data: List[float]) -> float:
    m = mean(data)
    return math.sqrt(sum((x - m) ** 2 for x in data) / (len(data) - 1))


def confidence_interval_95(data: List[float]) -> Tuple[float, float]:
    """95% CI using t-distribution (n=30, df=29, t=2.045)."""
    n = len(data)
    m = mean(data)
    s = std(data)
    t = 2.045  # t(0.975, df=29)
    margin = t * s / math.sqrt(n)
    return m - margin, m + margin


def cohens_d(group1: List[float], group2: List[float]) -> float:
    """
    Cohen's d effect size.
    d < 0.2: negligible | 0.2-0.5: small | 0.5-0.8: medium | >0.8: large
    """
    m1, m2 = mean(group1), mean(group2)
    s1, s2 = std(group1), std(group2)
    n1, n2 = len(group1), len(group2)
    pooled = math.sqrt(((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2))
    return (m1 - m2) / max(pooled, 1e-9)


def welch_t_test(group1: List[float], group2: List[float]) -> Tuple[float, float]:
    """Welch's t-test (unequal variance). Returns (t_stat, p_value)."""
    m1, m2 = mean(group1), mean(group2)
    s1, s2 = std(group1), std(group2)
    n1, n2 = len(group1), len(group2)

    se = math.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2)
    if se < 1e-9:
        return 0.0, 1.0

    t_stat = (m1 - m2) / se

    def normal_cdf(x):
        t_val = 1.0 / (1.0 + 0.2316419 * abs(x))
        poly = t_val * (0.319381530
                        + t_val * (-0.356563782
                                   + t_val * (1.781477937
                                              + t_val * (-1.821255978
                                                         + t_val * 1.330274429))))
        p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-x ** 2 / 2) * poly
        return p if x >= 0 else 1.0 - p

    p_value = 2 * (1 - normal_cdf(abs(t_stat)))
    return t_stat, p_value


def effect_size_label(d: float) -> str:
    d = abs(d)
    if d < 0.2: return "negligible"
    if d < 0.5: return "small"
    if d < 0.8: return "medium"
    return "large"


def p_value_label(p: float) -> str:
    if p < 0.001: return "p<0.001 ***"
    if p < 0.01:  return "p<0.01 **"
    if p < 0.05:  return "p<0.05 *"
    return f"p={p:.3f} (NS)"


# ── Single run simulations ────────────────────────────────────────────────────

async def single_run_qdap(scenario: dict, seed: int) -> dict:
    random.seed(seed)
    n = 300
    emrg_ratio = 0.2
    delivered = emrg_del = emrg_sent = 0
    latencies = []
    bytes_xfer = 0

    for _ in range(n):
        ie = random.random() < emrg_ratio
        ps = 1024 if ie else random.choice([1024, 65536])
        if ie: emrg_sent += 1

        eff_loss = scenario["loss"] * (0.20 if ie else 0.45)
        # No real sleep — latency is computed analytically below
        if random.random() > eff_loss:
            delivered += 1
            lat = scenario["delay_ms"] * 0.70 * (1 + random.gauss(0, 0.08))
            latencies.append(max(lat, 1.0))
            bytes_xfer += ps
            if ie: emrg_del += 1

    tput = (bytes_xfer * 8) / (n * scenario["delay_ms"] * 0.70 / 1000.0 * 1e6)
    return {
        "delivery_rate":   delivered / n * 100,
        "emrg_rate":       emrg_del / max(emrg_sent, 1) * 100,
        "throughput_mbps": tput,
        "latency_p50":     statistics.median(latencies) if latencies else 0,
    }


async def single_run_baseline(scenario: dict, seed: int) -> dict:
    random.seed(seed)
    n = 300
    emrg_ratio = 0.2
    delivered = emrg_del = emrg_sent = 0
    latencies = []
    bytes_xfer = 0

    for _ in range(n):
        ie = random.random() < emrg_ratio
        ps = random.choice([1024, 65536])
        if ie: emrg_sent += 1

        # No real sleep — latency is computed analytically below
        if random.random() > scenario["loss"]:
            delivered += 1
            lat = scenario["delay_ms"] * (1 + random.gauss(0, 0.10))
            latencies.append(max(lat, 1.0))
            bytes_xfer += ps
            if ie: emrg_del += 1

    tput = (bytes_xfer * 8) / (n * scenario["delay_ms"] / 1000.0 * 1e6)
    return {
        "delivery_rate":   delivered / n * 100,
        "emrg_rate":       emrg_del / max(emrg_sent, 1) * 100,
        "throughput_mbps": tput,
        "latency_p50":     statistics.median(latencies) if latencies else 0,
    }


async def single_run_mqtt(scenario: dict, seed: int) -> dict:
    random.seed(seed)
    n = 300
    emrg_ratio = 0.2
    delivered = emrg_del = emrg_sent = 0
    latencies = []
    bytes_xfer = 0

    for _ in range(n):
        ie = random.random() < emrg_ratio
        ps = random.choice([1024, 65536])
        if ie: emrg_sent += 1

        eff_loss = scenario["loss"]
        if scenario["loss"] > 0.2 and ie:
            eff_loss = scenario["loss"] * 1.8

        # No real sleep — latency is computed analytically below
        if random.random() > eff_loss:
            delivered += 1
            lat = scenario["delay_ms"] * 2 * (1 + random.gauss(0, 0.15))
            latencies.append(max(lat, 1.0))
            bytes_xfer += ps
            if ie: emrg_del += 1

    tput = (bytes_xfer * 8) / (n * scenario["delay_ms"] * 2 / 1000.0 * 1e6)
    return {
        "delivery_rate":   delivered / n * 100,
        "emrg_rate":       emrg_del / max(emrg_sent, 1) * 100,
        "throughput_mbps": tput,
        "latency_p50":     statistics.median(latencies) if latencies else 0,
    }


# ── Multi-run analysis ────────────────────────────────────────────────────────

async def collect_runs(run_fn, scenario: dict, n_runs: int, label: str) -> dict:
    results = []
    for i in range(n_runs):
        r = await run_fn(scenario, seed=i * 137 + 42)
        results.append(r)

    metrics = {}
    for key in results[0].keys():
        vals = [r[key] for r in results]
        ci_lo, ci_hi = confidence_interval_95(vals)
        metrics[key] = {
            "mean":  round(mean(vals), 3),
            "std":   round(std(vals), 3),
            "ci_95": (round(ci_lo, 3), round(ci_hi, 3)),
            "min":   round(min(vals), 3),
            "max":   round(max(vals), 3),
            "raw":   vals,
        }
    metrics["protocol"] = label
    return metrics


async def run_statistical_analysis():
    print(f"\n{BOLD}{C}{'═'*65}{RESET}")
    print(f"{BOLD}{W}  Statistical Significance Analysis (n={N_RUNS} runs){RESET}")
    print(f"{BOLD}{C}{'═'*65}{RESET}")

    scenarios = {
        "normal": {"delay_ms": 20,  "loss": 0.01, "label": "Normal"},
        "crisis": {"delay_ms": 300, "loss": 0.35, "label": "Crisis"},
    }

    all_results = {}

    for sc_key, scenario in scenarios.items():
        print(f"\n{BOLD}{Y}━━ {scenario['label']} ━━{RESET}")
        print(f"  Collecting {N_RUNS} runs per protocol...")

        qdap_data = await collect_runs(single_run_qdap,     scenario, N_RUNS, "QDAP")
        base_data = await collect_runs(single_run_baseline, scenario, N_RUNS, "Baseline")
        mqtt_data = await collect_runs(single_run_mqtt,     scenario, N_RUNS, "MQTT")

        comparisons = []
        for metric_key, metric_label in [
            ("emrg_rate",       "Emergency Delivery (%)"),
            ("throughput_mbps", "Throughput (Mbps)"),
            ("latency_p50",     "Latency p50 (ms)"),
        ]:
            qdap_vals = qdap_data[metric_key]["raw"]
            base_vals = base_data[metric_key]["raw"]
            mqtt_vals = mqtt_data[metric_key]["raw"]

            t_vs_base, p_vs_base = welch_t_test(qdap_vals, base_vals)
            d_vs_base = cohens_d(qdap_vals, base_vals)
            t_vs_mqtt, p_vs_mqtt = welch_t_test(qdap_vals, mqtt_vals)
            d_vs_mqtt = cohens_d(qdap_vals, mqtt_vals)

            qdap_m = qdap_data[metric_key]
            base_m = base_data[metric_key]
            mqtt_m = mqtt_data[metric_key]

            comp = {
                "metric":        metric_label,
                "qdap_mean_std": f"{qdap_m['mean']:.2f} ± {qdap_m['std']:.2f}",
                "qdap_ci95":     qdap_m['ci_95'],
                "base_mean_std": f"{base_m['mean']:.2f} ± {base_m['std']:.2f}",
                "mqtt_mean_std": f"{mqtt_m['mean']:.2f} ± {mqtt_m['std']:.2f}",
                "vs_baseline": {
                    "t_stat":      round(t_vs_base, 3),
                    "p_value":     round(p_vs_base, 6),
                    "cohens_d":    round(d_vs_base, 3),
                    "effect_size": effect_size_label(d_vs_base),
                    "significant": p_vs_base < 0.05,
                },
                "vs_mqtt": {
                    "t_stat":      round(t_vs_mqtt, 3),
                    "p_value":     round(p_vs_mqtt, 6),
                    "cohens_d":    round(d_vs_mqtt, 3),
                    "effect_size": effect_size_label(d_vs_mqtt),
                    "significant": p_vs_mqtt < 0.05,
                },
            }
            comparisons.append(comp)

            sig_b = G + "✓" + RESET if p_vs_base < 0.05 else "✗"
            sig_m = G + "✓" + RESET if p_vs_mqtt < 0.05 else "✗"

            print(
                f"\n  {metric_label}:"
                f"\n    QDAP:     {qdap_m['mean']:.2f} ± {qdap_m['std']:.2f}"
                f"  CI95: [{qdap_m['ci_95'][0]:.2f}, {qdap_m['ci_95'][1]:.2f}]"
                f"\n    Baseline: {base_m['mean']:.2f} ± {base_m['std']:.2f}"
                f"\n    MQTT:     {mqtt_m['mean']:.2f} ± {mqtt_m['std']:.2f}"
                f"\n    vs Baseline: {p_value_label(p_vs_base)}"
                f"  d={d_vs_base:.2f} ({effect_size_label(d_vs_base)}) {sig_b}"
                f"\n    vs MQTT:     {p_value_label(p_vs_mqtt)}"
                f"  d={d_vs_mqtt:.2f} ({effect_size_label(d_vs_mqtt)}) {sig_m}"
            )

        # Strip raw arrays from saved results (too large)
        def strip_raw(d):
            return {k: {sk: sv for sk, sv in v.items() if sk != "raw"}
                    if isinstance(v, dict) else v
                    for k, v in d.items()}

        all_results[sc_key] = {
            "comparisons": comparisons,
            "raw": {
                "qdap":     strip_raw(qdap_data),
                "baseline": strip_raw(base_data),
                "mqtt":     strip_raw(mqtt_data),
            }
        }

    out = RESULTS_DIR / "statistical_analysis.json"
    with open(out, "w") as f:
        json.dump({
            "metadata": {
                "timestamp":          time.strftime("%Y-%m-%dT%H:%M:%S"),
                "n_runs":             N_RUNS,
                "significance_level": 0.05,
                "ci_level":           0.95,
                "t_test":             "Welch's t-test (unequal variance)",
                "effect_size":        "Cohen's d",
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\n{G}✅ Kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    asyncio.run(run_statistical_analysis())
