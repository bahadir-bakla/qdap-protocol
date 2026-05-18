#!/usr/bin/env python3
"""
QDAP Bridge Benchmark — drop-in layer comparison

Compares bare MAC protocols against QDAP running on top of the same MAC.
Shows the application-layer gain from adding QDAP to an existing V2X stack.

Results are saved to results/bridge_results.csv (does NOT overwrite
arxiv_main.csv or v2x_results.csv).

Usage:
  python run_bridge_benchmark.py          # full 20-run
  python run_bridge_benchmark.py --quick  # 5-run smoke test
  python run_bridge_benchmark.py --stats-only  # print tables from saved CSV
"""

import argparse, csv, json, os, sys, time
import numpy as np
from scipy import stats as scipy_stats

sys.path.insert(0, os.path.dirname(__file__))

from simulation import run_scenario
from protocols import DSRCProtocol, CV2XProtocol, QDAPProtocol
from protocols_bridge import QDAPoverDSRC, QDAPoverCV2X

# ── Config ────────────────────────────────────────────────────────────────────

SCENARIOS  = ["urban", "highway", "cascade"]
DENSITIES  = [25, 50, 75, 100]
DURATION_S = 60

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
OUT_CSV  = os.path.join(RESULTS_DIR, "bridge_results.csv")
OUT_JSON = os.path.join(RESULTS_DIR, "bridge_significance.json")

# Protocol pairs: (baseline_cls, enhanced_cls, label)
PAIRS = [
    (DSRCProtocol,  QDAPoverDSRC, "DSRC"),
    (CV2XProtocol,  QDAPoverCV2X, "C-V2X"),
    (None,          QDAPProtocol, "standalone"),   # reference
]


def ci95(values):
    arr = np.array(values, dtype=float)
    if len(arr) < 2:
        return arr.mean(), 0.0
    se = scipy_stats.sem(arr)
    hw = se * scipy_stats.t.ppf(0.975, df=len(arr) - 1)
    return arr.mean(), hw


def run_suite(n_runs: int):
    """Run all protocol × scenario × density combinations."""
    fieldnames = [
        "scenario", "protocol", "n_agents", "run",
        "denm_pdr", "denm_ddl", "bsm_pdr", "emerg_p99", "latency_p99",
    ]

    total_combos = len(SCENARIOS) * len(DENSITIES) * 5  # 5 protocol entries
    done = 0
    t0 = time.time()

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for sc in SCENARIOS:
            for n in DENSITIES:
                # Run all protocol classes for this scenario×density
                all_cls = []
                for base_cls, enhanced_cls, _ in PAIRS:
                    if base_cls is not None:
                        all_cls.append(base_cls)
                    all_cls.append(enhanced_cls)

                for cls in all_cls:
                    runs = run_scenario(sc, cls, n_agents=n,
                                       n_runs=n_runs, seed=42,
                                       duration_s=DURATION_S)
                    for idx, r in enumerate(runs):
                        writer.writerow({
                            "scenario":   sc,
                            "protocol":   r.protocol_name,
                            "n_agents":   n,
                            "run":        idx,
                            "denm_pdr":   round(r.denm_pdr, 6),
                            "denm_ddl":   round(r.denm_deadline_rate, 6),
                            "bsm_pdr":    round(r.bsm_pdr, 6),
                            "emerg_p99":  round(r.emergency_p99, 4),
                            "latency_p99":round(r.latency_p99, 4),
                        })
                    f.flush()

                    done += 1
                    elapsed = time.time() - t0
                    eta = elapsed / done * (total_combos - done) if done else 0
                    print(f"  [{done:3d}/{total_combos}] {sc:8s} N={n:3d} "
                          f"{cls.name:18s} | {elapsed:5.0f}s  ETA {eta:.0f}s",
                          flush=True)

    print(f"\nSaved: {OUT_CSV}")


def compute_significance(df):
    """Mann-Whitney U: QDAP+X vs bare X for each scenario×density."""
    import pandas as pd
    sig = {}
    for sc in SCENARIOS:
        sig[sc] = {}
        for n in DENSITIES:
            sig[sc][str(n)] = {}
            sub = df[(df["scenario"] == sc) & (df["n_agents"] == n)]
            pairs_to_test = [
                ("QDAP+DSRC",  "DSRC 802.11p"),
                ("QDAP+C-V2X", "C-V2X Mode 4"),
            ]
            for enhanced_name, base_name in pairs_to_test:
                enh = sub[sub["protocol"] == enhanced_name]["denm_ddl"].values
                bas = sub[sub["protocol"] == base_name]["denm_ddl"].values
                if len(enh) < 2 or len(bas) < 2:
                    continue
                U, p = scipy_stats.mannwhitneyu(enh, bas, alternative="greater")
                m_enh, ci_enh = ci95(enh)
                m_bas, ci_bas = ci95(bas)
                sig[sc][str(n)][enhanced_name] = {
                    "vs":         base_name,
                    "enh_mean":   round(m_enh * 100, 2),
                    "enh_ci":     round(ci_enh * 100, 2),
                    "base_mean":  round(m_bas * 100, 2),
                    "base_ci":    round(ci_bas * 100, 2),
                    "delta_pp":   round((m_enh - m_bas) * 100, 2),
                    "U": U, "p": round(p, 5),
                    "sig": "***" if p < 0.001 else ("**" if p < 0.01
                            else ("*" if p < 0.05 else "ns")),
                }
    return sig


def print_tables(csv_path: str):
    """Print summary tables from saved CSV."""
    import pandas as pd
    df = pd.read_csv(csv_path)

    print("\n" + "=" * 72)
    print("  QDAP DROP-IN LAYER — GAIN OVER BARE MAC")
    print("  Urban Intersection, 20 runs, mean ± 95% CI")
    print("=" * 72)

    pairs_display = [
        ("DSRC 802.11p",  "QDAP+DSRC"),
        ("C-V2X Mode 4",  "QDAP+C-V2X"),
    ]

    for n in DENSITIES:
        print(f"\n  N = {n}:")
        print(f"  {'Protocol':<20} {'DENM PDR':>10} {'<50ms':>8} {'Emerg p99':>10} {'Gain':>8}")
        print(f"  {'-'*20} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")
        sub = df[(df["scenario"] == "urban") & (df["n_agents"] == n)]

        # QDAP standalone reference
        qdap_ref = sub[sub["protocol"] == "QDAP"]
        if len(qdap_ref):
            m_pdr, _ = ci95(qdap_ref["denm_pdr"])
            m_ddl, _ = ci95(qdap_ref["denm_ddl"])
            m_p99, _ = ci95(qdap_ref["emerg_p99"])
            print(f"  {'QDAP (standalone)':<20} {m_pdr*100:>9.1f}%"
                  f" {m_ddl*100:>7.1f}% {m_p99:>9.1f}ms {'ref':>8}")

        for base_name, enh_name in pairs_display:
            base_r = sub[sub["protocol"] == base_name]
            enh_r  = sub[sub["protocol"] == enh_name]
            if len(base_r) == 0 or len(enh_r) == 0:
                continue
            mb, _ = ci95(base_r["denm_ddl"])
            me, _ = ci95(enh_r["denm_ddl"])
            m_pdr, _ = ci95(enh_r["denm_pdr"])
            m_p99, _ = ci95(enh_r["emerg_p99"])
            gain = (me - mb) * 100
            print(f"  {base_name:<20} {ci95(base_r['denm_pdr'])[0]*100:>9.1f}%"
                  f" {mb*100:>7.1f}%"
                  f" {ci95(base_r['emerg_p99'])[0]:>9.1f}ms {'':>8}")
            print(f"  {enh_name:<20} {m_pdr*100:>9.1f}%"
                  f" {me*100:>7.1f}%"
                  f" {m_p99:>9.1f}ms {gain:>+7.1f}pp")

    # Significance
    sig = compute_significance(df)
    print("\n" + "=" * 72)
    print("  STATISTICAL SIGNIFICANCE — QDAP+X vs bare X (DENM deadline)")
    print("  Urban N=75, Mann-Whitney U (one-sided, QDAP+X > bare X)")
    print("=" * 72)
    for k, v in sig["urban"]["75"].items():
        print(f"  {k} vs {v['vs']}: "
              f"Δ={v['delta_pp']:+.1f}pp  p={v['p']:.5f}  {v['sig']}")

    with open(OUT_JSON, "w") as f:
        json.dump(sig, f, indent=2)
    print(f"\nSignificance saved: {OUT_JSON}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick",      action="store_true",
                        help="5 runs instead of 20 (smoke test)")
    parser.add_argument("--stats-only", action="store_true",
                        help="Print tables from existing bridge_results.csv")
    args = parser.parse_args()

    if args.stats_only:
        import pandas as pd
        if not os.path.exists(OUT_CSV):
            print(f"ERROR: {OUT_CSV} not found. Run without --stats-only first.")
            sys.exit(1)
        print_tables(OUT_CSV)
    else:
        n_runs = 5 if args.quick else 20
        print("=" * 60)
        print("  QDAP Bridge Benchmark")
        print(f"  Runs     : {n_runs} per combination")
        print(f"  Scenarios: {SCENARIOS}")
        print(f"  Densities: {DENSITIES}")
        print("=" * 60)
        run_suite(n_runs)

        import pandas as pd
        print_tables(OUT_CSV)
