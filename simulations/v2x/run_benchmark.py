#!/usr/bin/env python3
"""
QDAP V2X Research Benchmark
============================
Simulates 50-100 vehicles (cars, motorcycles, pedestrians) in V2V/V2X
scenarios. Compares QDAP vs DSRC/802.11p, 802.11bd, C-V2X Mode 4,
UDP, and MQTT across three traffic scenarios.

Scenarios
---------
urban   : 400m x 400m intersection with buildings and mixed traffic
highway : 2km dual-lane highway platoon at motorway speeds
cascade : highway + pedestrian hazard → emergency DENM propagation chain

Usage
-----
    python run_benchmark.py                    # full benchmark (~5-20 min)
    python run_benchmark.py --quick            # quick run (~2 min)
    python run_benchmark.py --scenario urban   # single scenario
    python run_benchmark.py --scenario cascade --quick

Output
------
    simulations/v2x/results/v2x_benchmark.pdf   publication-quality figures
    simulations/v2x/results/v2x_results.csv      raw data for reproducibility
"""
import argparse
import csv
import os
import sys
import time
from typing import Dict, List

import numpy as np

# Ensure this directory is on the path regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulation import run_scenario, ALL_PROTOCOLS, SimMetrics
from protocols import (
    QDAPProtocol, DSRCProtocol, IEEE80211bdProtocol,
    CV2XProtocol, UDPProtocol, MQTTProtocol,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

PROTOCOL_CLASSES = [
    QDAPProtocol,
    DSRCProtocol,
    IEEE80211bdProtocol,
    CV2XProtocol,
    UDPProtocol,
    MQTTProtocol,
]

# Full benchmark
DENSITY_SWEEP = [10, 25, 50, 75, 100]
N_RUNS_FULL = 5

# Quick smoke-test
DENSITY_QUICK = [25, 50, 100]
N_RUNS_QUICK = 2


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def run_all(
    scenarios: List[str],
    densities: List[int],
    n_runs: int,
    seed: int = 42,
    duration_s: float = 20.0,
) -> Dict[str, Dict[int, List[SimMetrics]]]:
    """
    Run all (scenario, density, protocol) combinations.
    Returns nested dict: {scenario: {n_agents: [SimMetrics, ...]}}
    """
    all_results: Dict[str, Dict[int, List[SimMetrics]]] = {
        s: {} for s in scenarios
    }

    total = len(scenarios) * len(densities) * len(PROTOCOL_CLASSES) * n_runs
    done = 0
    t0 = time.time()

    for scenario in scenarios:
        for n in densities:
            for proto_cls in PROTOCOL_CLASSES:
                runs = run_scenario(scenario, proto_cls, n, n_runs, seed,
                                    duration_s=duration_s)
                all_results[scenario].setdefault(n, []).extend(runs)
                done += n_runs

                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0.0
                print(
                    f"  [{done:>4}/{total}] "
                    f"{scenario:<8} n={n:>3}  "
                    f"{proto_cls.name:<16} "
                    f"| elapsed {elapsed:>5.0f}s  ETA {eta:>5.0f}s"
                )

    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def save_csv(
    all_results: Dict[str, Dict[int, List[SimMetrics]]],
    path: str,
) -> None:
    """Dump all SimMetrics to a flat CSV for downstream analysis / reproducibility."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "scenario", "protocol", "n_agents", "run",
            "bsm_sent", "bsm_delivered", "bsm_pdr",
            "bsm_deadline_rate",
            "denm_sent", "denm_delivered", "denm_pdr",
            "denm_deadline_rate",
            "latency_p50_ms", "latency_p99_ms", "emergency_p99_ms",
            "mean_cbr",
            "cascade_n_vehicles",
            "cascade_mean_ms", "cascade_p95_ms",
        ])
        for scenario, by_density in all_results.items():
            for n, runs in by_density.items():
                for i, r in enumerate(runs):
                    casc_vals = r.cascade_times_ms
                    writer.writerow([
                        scenario,
                        r.protocol_name,
                        n,
                        i,
                        r.bsm_sent,
                        r.bsm_delivered,
                        f"{r.bsm_pdr:.4f}",
                        f"{r.bsm_deadline_rate:.4f}",
                        r.denm_sent,
                        r.denm_delivered,
                        f"{r.denm_pdr:.4f}",
                        f"{r.denm_deadline_rate:.4f}",
                        f"{r.latency_p50:.2f}",
                        f"{r.latency_p99:.2f}",
                        f"{r.emergency_p99:.2f}",
                        (
                            f"{float(np.mean(r.cbr_samples)):.4f}"
                            if r.cbr_samples else "0"
                        ),
                        len(casc_vals),
                        (
                            f"{float(np.mean(casc_vals)):.2f}"
                            if casc_vals else ""
                        ),
                        (
                            f"{float(np.percentile(casc_vals, 95)):.2f}"
                            if casc_vals else ""
                        ),
                    ])
    print(f"[csv]  Saved: {path}")


def print_summary_table(
    all_results: Dict[str, Dict[int, List[SimMetrics]]],
) -> None:
    """Print a terminal summary table for N=80 (or nearest available density)."""
    print("\n" + "=" * 96)
    print(f"{'QDAP V2X BENCHMARK SUMMARY':^96}")
    print("=" * 96)
    hdr = (
        f"  {'Protocol':<20} {'Scenario':<12} "
        f"{'DENM PDR':>10} {'DENM<50ms':>11} "
        f"{'BSM PDR':>9} {'emer p99':>10}  {'mean CBR':>9}"
    )
    print(hdr)
    print("-" * 96)

    for scenario in ["urban", "highway", "cascade"]:
        by_density = all_results.get(scenario, {})
        if not by_density:
            continue

        # Pick the density closest to 80
        target_n = min(by_density.keys(), key=lambda x: abs(x - 80))
        n80_runs = by_density.get(target_n, [])

        # Aggregate per protocol
        proto_buckets: Dict[str, List[SimMetrics]] = {}
        for r in n80_runs:
            proto_buckets.setdefault(r.protocol_name, []).append(r)

        for proto_name in [
            "QDAP", "DSRC 802.11p", "802.11bd",
            "C-V2X Mode 4", "UDP", "MQTT",
        ]:
            runs = proto_buckets.get(proto_name)
            if not runs:
                continue
            pdr_d  = float(np.mean([r.denm_pdr          for r in runs])) * 100
            ddl_d  = float(np.mean([r.denm_deadline_rate for r in runs])) * 100
            pdr_b  = float(np.mean([r.bsm_pdr            for r in runs])) * 100
            p99_e  = float(np.mean([r.emergency_p99      for r in runs]))
            cbr    = float(np.mean([
                np.mean(r.cbr_samples) if r.cbr_samples else 0
                for r in runs
            ]))
            star = " *" if proto_name == "QDAP" else "  "
            print(
                f"  {proto_name + star:<20} {scenario:<12} "
                f"{pdr_d:>9.1f}% {ddl_d:>10.1f}% "
                f"{pdr_b:>8.1f}% {p99_e:>9.1f}ms "
                f"  {cbr:>7.3f}"
            )
        print()

    print("=" * 96)
    print("  * QDAP")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QDAP V2X Protocol Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick run: fewer densities and Monte-Carlo repetitions (~2 min)",
    )
    parser.add_argument(
        "--scenario",
        choices=["urban", "highway", "cascade", "all"],
        default="all",
        help="Scenario(s) to simulate (default: all)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Base random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--no-pdf", action="store_true",
        help="Skip PDF generation (useful in headless environments without LaTeX fonts)",
    )
    args = parser.parse_args()

    densities  = DENSITY_QUICK if args.quick else DENSITY_SWEEP
    n_runs     = N_RUNS_QUICK  if args.quick else N_RUNS_FULL
    duration_s = 5.0 if args.quick else 20.0
    scenarios  = (
        ["urban", "highway", "cascade"]
        if args.scenario == "all"
        else [args.scenario]
    )

    print(f"\n{'=' * 62}")
    print(f"  QDAP V2X Research Benchmark")
    print(f"  Scenarios : {scenarios}")
    print(f"  Densities : {densities}")
    print(f"  Duration  : {duration_s}s per run")
    print(f"  Runs each : {n_runs}  (Monte-Carlo)")
    print(f"  Protocols : {len(PROTOCOL_CLASSES)}")
    print(f"  Seed      : {args.seed}")
    print(f"{'=' * 62}\n")

    t0 = time.time()
    all_results = run_all(scenarios, densities, n_runs, args.seed, duration_s=duration_s)
    elapsed = time.time() - t0

    print(f"\n[done] Simulation complete in {elapsed:.1f}s")

    print_summary_table(all_results)

    # ── Save CSV ──────────────────────────────────────────────────────
    os.makedirs(RESULTS_DIR, exist_ok=True)
    csv_path = os.path.join(RESULTS_DIR, "v2x_results.csv")
    save_csv(all_results, csv_path)

    # ── Generate PDF ──────────────────────────────────────────────────
    pdf_path = os.path.join(RESULTS_DIR, "v2x_benchmark.pdf")
    if not args.no_pdf:
        try:
            from plots import generate_pdf
            # Ensure all three scenario keys exist (even if empty) for the PDF
            for s in ["urban", "highway", "cascade"]:
                all_results.setdefault(s, {})
            generate_pdf(all_results, pdf_path)
        except Exception as exc:
            print(f"[warn] PDF generation failed: {exc}")
            print(f"       Run with --no-pdf to suppress this warning.")
    else:
        print("[pdf]  Skipped (--no-pdf)")

    # ── Final output summary ──────────────────────────────────────────
    print(f"\n[output]")
    print(f"  CSV : {csv_path}")
    if not args.no_pdf:
        print(f"  PDF : {pdf_path}")
    print(f"\nTo view results:")
    print(f"  open {pdf_path}")
    print(f"  open {csv_path}")


if __name__ == "__main__":
    main()
