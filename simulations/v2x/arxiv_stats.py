#!/usr/bin/env python3
"""
ArXiv Statistical Analysis — Faz 1
=====================================
1. 20-run Monte Carlo (5→20 run, tighter CI)
2. 95% Confidence intervals per metric
3. Mann-Whitney U significance tests (QDAP vs each competitor)
4. Ablation study (4 bileşeni tek tek kapat)

Usage:
    python arxiv_stats.py               # full 20-run (slow, ~2 hours)
    python arxiv_stats.py --quick       # 5-run quick verify
    python arxiv_stats.py --ablation-only
    python arxiv_stats.py --stats-only  # stats from existing CSV
"""
import argparse
import csv
import os
import sys
import time
import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

import numpy as np
from scipy import stats as scipy_stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulation import run_scenario, SimMetrics, ALL_PROTOCOLS
from protocols import (
    QDAPProtocol, DSRCProtocol, IEEE80211bdProtocol,
    CV2XProtocol, UDPProtocol, MQTTProtocol,
)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
N_RUNS_ARXIV = 20
N_RUNS_QUICK = 5
SEED_BASE    = 42
DENSITIES    = [25, 50, 75, 100]   # focus on realistic urban/highway densities
SCENARIOS    = ["urban", "highway", "cascade"]
DURATION_S   = 20.0


# ─────────────────────────────────────────────────────────────────────────────
# Faz 1.4 — Ablation variants of QDAPProtocol
# ─────────────────────────────────────────────────────────────────────────────

class QDAPNoFEC(QDAPProtocol):
    """QDAP without Adaptive FEC — always k=1 r=0 (no redundancy)."""
    name = "QDAP-noFEC"
    color = "#06D6A0"

    def _p_fail(self, per_base, loss, is_emerg):
        return per_base  # no FEC


class QDAPNoScheduler(QDAPProtocol):
    """QDAP without priority scheduler — all messages get same queue delay."""
    name = "QDAP-noSched"
    color = "#06D6A0"

    def deliver(self, msg, snr_db, cbr, n_vehicles, rng):
        from messages import Priority, MsgType
        from channel import snr_to_per
        from protocols import DeliveryResult

        per_base = snr_to_per(snr_db, "qdap")
        is_emerg = (msg.priority == Priority.EMERGENCY or msg.msg_type == MsgType.DENM)
        loss = self._observed_loss(msg.src_id)
        p_fail = self._p_fail(per_base, loss, is_emerg)

        # No priority — all traffic treated equally (uniform 2ms delay)
        sched_ms = rng.exponential(2.0)

        eff_bytes = msg.payload_bytes * 0.256 if msg.msg_type == MsgType.BSM else msg.payload_bytes
        tx_ms = eff_bytes * 8 / (20.0 * 1e6) * 1000

        delivered = rng.random() > p_fail
        self._update(msg.src_id, not delivered)
        if delivered:
            return DeliveryResult(True, sched_ms + tx_ms + rng.exponential(0.3), "ok")
        return DeliveryResult(False, 0.0, "channel")


class QDAPNoDelta(QDAPProtocol):
    """QDAP without Delta Encoder — full BSM size (400B, no compression)."""
    name = "QDAP-noDelta"
    color = "#06D6A0"

    def deliver(self, msg, snr_db, cbr, n_vehicles, rng):
        from messages import Priority, MsgType
        from channel import snr_to_per
        from protocols import DeliveryResult

        per_base = snr_to_per(snr_db, "qdap")
        is_emerg = (msg.priority == Priority.EMERGENCY or msg.msg_type == MsgType.DENM)
        loss = self._observed_loss(msg.src_id)
        p_fail = self._p_fail(per_base, loss, is_emerg)

        if is_emerg:        sched_ms = rng.exponential(0.5)
        elif msg.priority.value >= 2:  sched_ms = rng.exponential(1.0)
        else:               sched_ms = rng.exponential(2.0) * (1 + cbr * 0.5)

        # No delta encoding — full payload size
        tx_ms = msg.payload_bytes * 8 / (20.0 * 1e6) * 1000

        delivered = rng.random() > p_fail
        self._update(msg.src_id, not delivered)
        if delivered:
            return DeliveryResult(True, sched_ms + tx_ms + rng.exponential(0.3), "ok")
        return DeliveryResult(False, 0.0, "channel")


class QDAPNoGhost(QDAPProtocol):
    """QDAP without Ghost Session — adds TCP-like reconnect overhead."""
    name = "QDAP-noGhost"
    color = "#06D6A0"

    def deliver(self, msg, snr_db, cbr, n_vehicles, rng):
        from messages import Priority, MsgType
        from channel import snr_to_per
        from protocols import DeliveryResult

        per_base = snr_to_per(snr_db, "qdap")
        is_emerg = (msg.priority == Priority.EMERGENCY or msg.msg_type == MsgType.DENM)
        loss = self._observed_loss(msg.src_id)
        p_fail = self._p_fail(per_base, loss, is_emerg)

        if is_emerg:        sched_ms = rng.exponential(0.5)
        elif msg.priority.value >= 2:  sched_ms = rng.exponential(1.0)
        else:               sched_ms = rng.exponential(2.0) * (1 + cbr * 0.5)

        eff_bytes = msg.payload_bytes * 0.256 if msg.msg_type == MsgType.BSM else msg.payload_bytes
        tx_ms = eff_bytes * 8 / (20.0 * 1e6) * 1000

        # Ghost session removed: occasional reconnect RTT penalty (~1-RTT TCP SYN)
        reconnect_ms = rng.exponential(15.0) if rng.random() < 0.05 else 0.0

        delivered = rng.random() > p_fail
        self._update(msg.src_id, not delivered)
        if delivered:
            lat = sched_ms + tx_ms + reconnect_ms + rng.exponential(0.3)
            return DeliveryResult(True, lat, "ok")
        return DeliveryResult(False, 0.0, "channel")


ABLATION_CLASSES = [
    QDAPProtocol,     # full QDAP
    QDAPNoFEC,
    QDAPNoScheduler,
    QDAPNoDelta,
    QDAPNoGhost,
]

MAIN_CLASSES = [
    QDAPProtocol, DSRCProtocol, IEEE80211bdProtocol,
    CV2XProtocol, UDPProtocol, MQTTProtocol,
]


# ─────────────────────────────────────────────────────────────────────────────
# Statistical helpers
# ─────────────────────────────────────────────────────────────────────────────

def mean_ci_95(values: List[float]) -> Tuple[float, float]:
    """Returns (mean, half_width_95ci). Requires scipy."""
    a = np.array(values)
    n = len(a)
    if n < 2:
        return float(a.mean()), 0.0
    se = scipy_stats.sem(a)
    h  = se * scipy_stats.t.ppf(0.975, df=n - 1)
    return float(a.mean()), float(h)


def mannwhitney(a: List[float], b: List[float]) -> Tuple[float, float, str]:
    """Mann-Whitney U test. Returns (U, p_value, significance)."""
    if len(a) < 3 or len(b) < 3:
        return 0.0, 1.0, "n/a"
    u, p = scipy_stats.mannwhitneyu(a, b, alternative="greater")
    sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
    return float(u), float(p), sig


def extract_metric(runs: List[SimMetrics], key: str) -> List[float]:
    mapping = {
        "denm_pdr":  lambda r: r.denm_pdr * 100,
        "denm_ddl":  lambda r: r.denm_deadline_rate * 100,
        "bsm_pdr":   lambda r: r.bsm_pdr * 100,
        "emerg_p99": lambda r: r.emergency_p99,
    }
    fn = mapping[key]
    return [fn(r) for r in runs if fn(r) > 0]


# ─────────────────────────────────────────────────────────────────────────────
# Run helpers
# ─────────────────────────────────────────────────────────────────────────────

def run_suite(
    proto_classes, scenarios, densities, n_runs, duration_s, label="main"
) -> Dict:
    """
    Returns nested dict: {scenario: {n_agents: {proto_name: [SimMetrics]}}}
    """
    results = {s: {n: {} for n in densities} for s in scenarios}
    total = len(scenarios) * len(densities) * len(proto_classes) * n_runs
    done  = 0
    t0    = time.time()

    for sc in scenarios:
        for n in densities:
            for cls in proto_classes:
                runs = run_scenario(sc, cls, n, n_runs, SEED_BASE, duration_s)
                results[sc][n][cls.name] = runs
                done += n_runs
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0
                print(f"  [{label}] [{done:>4}/{total}] "
                      f"{sc:<8} N={n:>3} {cls.name:<18} "
                      f"| {elapsed:>5.0f}s  ETA {eta:>5.0f}s")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Faz 1.2 + 1.3 — CI & significance table
# ─────────────────────────────────────────────────────────────────────────────

def print_ci_table(results: Dict, scenario: str = "urban", n: int = 75):
    print(f"\n{'='*90}")
    print(f"  95% Confidence Intervals — {scenario.upper()} N={n}")
    print(f"{'='*90}")
    print(f"  {'Protocol':<18} {'DENM PDR':>14} {'<50ms':>14} {'BSM PDR':>14} {'Emerg p99':>12}")
    print(f"  {'-'*72}")

    proto_data = results.get(scenario, {}).get(n, {})
    for proto_name in ["QDAP", "DSRC 802.11p", "802.11bd",
                       "C-V2X Mode 4", "UDP", "MQTT"]:
        runs = proto_data.get(proto_name)
        if not runs:
            continue
        dm, dh   = mean_ci_95(extract_metric(runs, "denm_pdr"))
        ddm, ddh = mean_ci_95(extract_metric(runs, "denm_ddl"))
        bm, bh   = mean_ci_95(extract_metric(runs, "bsm_pdr"))
        pm, ph   = mean_ci_95(extract_metric(runs, "emerg_p99"))
        star = " *" if proto_name == "QDAP" else "  "
        print(f"  {proto_name+star:<18} "
              f"{dm:>6.1f}±{dh:>4.1f}%  "
              f"{ddm:>6.1f}±{ddh:>4.1f}%  "
              f"{bm:>6.1f}±{bh:>4.1f}%  "
              f"{pm:>7.1f}±{ph:>4.1f}ms")

    print()
    # Significance tests: QDAP vs each
    qdap_runs = proto_data.get("QDAP", [])
    if not qdap_runs:
        return
    qdap_denm = extract_metric(qdap_runs, "denm_pdr")
    print(f"  Mann-Whitney U  (QDAP > X, DENM PDR, one-sided):")
    for comp in ["802.11bd", "DSRC 802.11p", "C-V2X Mode 4", "UDP", "MQTT"]:
        comp_runs = proto_data.get(comp, [])
        if not comp_runs:
            continue
        comp_denm = extract_metric(comp_runs, "denm_pdr")
        u, p, sig = mannwhitney(qdap_denm, comp_denm)
        print(f"    QDAP > {comp:<18}  U={u:>6.0f}  p={p:.4f}  {sig}")


# ─────────────────────────────────────────────────────────────────────────────
# Faz 1.4 — Ablation table
# ─────────────────────────────────────────────────────────────────────────────

def print_ablation_table(results: Dict, scenario: str = "urban", n: int = 75):
    print(f"\n{'='*90}")
    print(f"  ABLATION STUDY — {scenario.upper()} N={n}  (each component removed)")
    print(f"{'='*90}")
    print(f"  {'Variant':<20} {'DENM PDR':>14} {'<50ms':>14} {'Emerg p99':>12}  {'ΔDENM PDR':>10}")
    print(f"  {'-'*72}")

    proto_data = results.get(scenario, {}).get(n, {})
    full_runs  = proto_data.get("QDAP", [])
    full_mean  = float(np.mean(extract_metric(full_runs, "denm_pdr"))) if full_runs else 0.0

    ablation_order = ["QDAP", "QDAP-noFEC", "QDAP-noSched",
                      "QDAP-noDelta", "QDAP-noGhost"]
    for variant in ablation_order:
        runs = proto_data.get(variant, [])
        if not runs:
            continue
        dm, dh   = mean_ci_95(extract_metric(runs, "denm_pdr"))
        ddm, ddh = mean_ci_95(extract_metric(runs, "denm_ddl"))
        pm, ph   = mean_ci_95(extract_metric(runs, "emerg_p99"))
        delta    = dm - full_mean if variant != "QDAP" else 0.0
        delta_s  = f"{delta:+.1f}pp" if variant != "QDAP" else "  —"
        print(f"  {variant:<20} "
              f"{dm:>6.1f}±{dh:>4.1f}%  "
              f"{ddm:>6.1f}±{ddh:>4.1f}%  "
              f"{pm:>7.1f}±{ph:>4.1f}ms  "
              f"{delta_s:>10}")


# ─────────────────────────────────────────────────────────────────────────────
# CSV export
# ─────────────────────────────────────────────────────────────────────────────

def save_arxiv_csv(results: Dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "scenario", "protocol", "n_agents", "run",
            "denm_pdr", "denm_ddl", "bsm_pdr",
            "emerg_p99", "latency_p99", "mean_cbr",
        ])
        for sc, by_n in results.items():
            for n, by_proto in by_n.items():
                for proto, runs in by_proto.items():
                    for i, r in enumerate(runs):
                        w.writerow([
                            sc, proto, n, i,
                            f"{r.denm_pdr:.5f}",
                            f"{r.denm_deadline_rate:.5f}",
                            f"{r.bsm_pdr:.5f}",
                            f"{r.emergency_p99:.3f}",
                            f"{r.latency_p99:.3f}",
                            f"{float(np.mean(r.cbr_samples)) if r.cbr_samples else 0:.4f}",
                        ])
    print(f"\n[csv] {path}")


def save_significance_json(results: Dict, path: str):
    """Save all significance test results as JSON for LaTeX table generation."""
    out = {}
    for sc in SCENARIOS:
        out[sc] = {}
        for n in DENSITIES:
            out[sc][str(n)] = {}
            proto_data = results.get(sc, {}).get(n, {})
            qdap_runs = proto_data.get("QDAP", [])
            if not qdap_runs:
                continue
            qdap_denm = extract_metric(qdap_runs, "denm_pdr")
            qdap_ddl  = extract_metric(qdap_runs, "denm_ddl")
            for proto_name in ["802.11bd", "DSRC 802.11p", "C-V2X Mode 4", "UDP", "MQTT"]:
                comp_runs = proto_data.get(proto_name, [])
                if not comp_runs:
                    continue
                comp_denm = extract_metric(comp_runs, "denm_pdr")
                u_d, p_d, sig_d = mannwhitney(qdap_denm, comp_denm)
                dm_q, dh_q = mean_ci_95(qdap_denm)
                dm_c, dh_c = mean_ci_95(comp_denm)
                out[sc][str(n)][proto_name] = {
                    "qdap_mean": round(dm_q, 2), "qdap_ci": round(dh_q, 2),
                    "comp_mean": round(dm_c, 2), "comp_ci": round(dh_c, 2),
                    "U": round(u_d, 1), "p": round(p_d, 5), "sig": sig_d,
                    "delta_pp": round(dm_q - dm_c, 2),
                }
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"[json] {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ArXiv statistical analysis pipeline")
    parser.add_argument("--quick",        action="store_true", help="5 runs instead of 20")
    parser.add_argument("--ablation-only",action="store_true", help="Only run ablation study")
    parser.add_argument("--stats-only",   action="store_true", help="Load existing CSV, compute stats only")
    parser.add_argument("--n",            type=int, default=75, help="N agents for display tables")
    parser.add_argument("--scenario",     default="urban", choices=SCENARIOS + ["all"])
    args = parser.parse_args()

    n_runs    = N_RUNS_QUICK if args.quick else N_RUNS_ARXIV
    scenarios = SCENARIOS if args.scenario == "all" else [args.scenario]

    print(f"\n{'='*60}")
    print(f"  QDAP ArXiv Statistical Pipeline")
    print(f"  Runs     : {n_runs} per combination")
    print(f"  Scenarios: {scenarios}")
    print(f"  Densities: {DENSITIES}")
    print(f"  Quick    : {args.quick}")
    print(f"{'='*60}\n")

    t0 = time.time()

    if args.ablation_only:
        print(">>> Faz 1.4 — Ablation study only")
        results = run_suite(ABLATION_CLASSES, scenarios, DENSITIES, n_runs, DURATION_S, "ablation")
        for sc in scenarios:
            print_ablation_table(results, sc, args.n)
        save_arxiv_csv(results, os.path.join(RESULTS_DIR, "arxiv_ablation.csv"))
        return

    # Full pipeline
    print(">>> Faz 1.1-1.3 — Main protocols (20 runs)")
    main_results = run_suite(MAIN_CLASSES, scenarios, DENSITIES, n_runs, DURATION_S, "main")

    print("\n>>> Faz 1.4 — Ablation study")
    ablation_results = run_suite(ABLATION_CLASSES, scenarios, DENSITIES, n_runs, DURATION_S, "ablation")

    print(f"\n[done] Total time: {time.time()-t0:.0f}s")

    # ── Display tables ────────────────────────────────────────────────────────
    for sc in scenarios:
        print_ci_table(main_results, sc, args.n)

    for sc in scenarios:
        print_ablation_table(ablation_results, sc, args.n)

    # ── Save outputs ──────────────────────────────────────────────────────────
    os.makedirs(RESULTS_DIR, exist_ok=True)
    save_arxiv_csv(main_results,
                   os.path.join(RESULTS_DIR, "arxiv_main.csv"))
    save_arxiv_csv(ablation_results,
                   os.path.join(RESULTS_DIR, "arxiv_ablation.csv"))
    save_significance_json(main_results,
                           os.path.join(RESULTS_DIR, "arxiv_significance.json"))

    print(f"\n[output]")
    print(f"  results/arxiv_main.csv")
    print(f"  results/arxiv_ablation.csv")
    print(f"  results/arxiv_significance.json")
    print(f"\nNext: python arxiv_stats.py --stats-only   (reprint tables without re-running)")


if __name__ == "__main__":
    main()
