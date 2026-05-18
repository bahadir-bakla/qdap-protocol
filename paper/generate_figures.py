#!/usr/bin/env python3
"""
Generate publication-quality figures for QDAP ArXiv paper.
Output: paper/figures/*.pdf  (vector, LaTeX-compatible)
"""

import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from scipy import stats

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Use 20-run arxiv data if available, else fall back to 5-run v2x_results
_arxiv = os.path.join(SCRIPT_DIR, "..", "simulations", "v2x", "results", "arxiv_main.csv")
_v2x   = os.path.join(SCRIPT_DIR, "..", "simulations", "v2x", "results", "v2x_results.csv")
CSV_PATH = _arxiv if os.path.exists(_arxiv) else _v2x
OUT_DIR    = os.path.join(SCRIPT_DIR, "figures")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Style ──────────────────────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family":      "serif",
    "font.serif":       ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size":        9,
    "axes.labelsize":   9,
    "axes.titlesize":   9,
    "legend.fontsize":  7.5,
    "xtick.labelsize":  8,
    "ytick.labelsize":  8,
    "figure.dpi":       300,
    "axes.linewidth":   0.8,
    "axes.grid":        True,
    "grid.alpha":       0.3,
    "grid.linewidth":   0.5,
    "lines.linewidth":  1.4,
    "lines.markersize": 5,
    "pdf.fonttype":     42,   # embed fonts
    "ps.fonttype":      42,
})

PROTO_ORDER  = ["QDAP", "802.11bd", "DSRC 802.11p", "C-V2X Mode 4", "UDP", "MQTT"]
PROTO_LABELS = ["QDAP", "802.11bd", "DSRC/802.11p", "C-V2X (SPS)", "UDP", "MQTT"]
COLORS = {
    "QDAP":         "#2196F3",
    "802.11bd":     "#4CAF50",
    "DSRC 802.11p": "#FF9800",
    "C-V2X Mode 4": "#9C27B0",
    "UDP":          "#F44336",
    "MQTT":         "#795548",
}
MARKERS = {
    "QDAP":         "o",
    "802.11bd":     "s",
    "DSRC 802.11p": "^",
    "C-V2X Mode 4": "D",
    "UDP":          "v",
    "MQTT":         "P",
}

def ci95(values):
    """Return (mean, half_width) 95% CI."""
    arr = np.array(values, dtype=float)
    n = len(arr)
    if n < 2:
        return arr.mean(), 0.0
    se = stats.sem(arr)
    hw = se * stats.t.ppf(0.975, df=n - 1)
    return arr.mean(), hw

def save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {name}")

# ── Load data ──────────────────────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)

# Normalize column names: arxiv_main.csv uses shorter names
if "denm_ddl" in df.columns:
    df = df.rename(columns={
        "denm_ddl":      "denm_deadline_rate",
        "emerg_p99":     "emergency_p99_ms",
        "latency_p99":   "latency_p99_ms",
        "bsm_pdr":       "bsm_pdr",
    })
if "denm_pdr" not in df.columns and "bsm_pdr" not in df.columns:
    raise RuntimeError("Unrecognized CSV schema")

DENSITIES = sorted(df["n_agents"].unique())

# ══════════════════════════════════════════════════════════════════════════════
# Figure 1 — PDR vs Vehicle Density (urban, BSM + DENM side-by-side)
# ══════════════════════════════════════════════════════════════════════════════
def fig_pdr_density():
    urban = df[df["scenario"] == "urban"]
    dens  = sorted(urban["n_agents"].unique())

    fig, axes = plt.subplots(1, 2, figsize=(6.5, 2.5), sharey=False)

    for ax, metric, ylabel, title in zip(
        axes,
        ["bsm_pdr",  "denm_pdr"],
        ["PDR",      "DENM PDR"],
        ["(a) BSM Packet Delivery Ratio", "(b) DENM Packet Delivery Ratio"],
    ):
        for proto in PROTO_ORDER:
            means, hws = [], []
            for n in dens:
                rows = urban[(urban["protocol"] == proto) & (urban["n_agents"] == n)]["bsm_pdr" if metric == "bsm_pdr" else "denm_pdr"]
                if len(rows) == 0:
                    means.append(np.nan); hws.append(0.0)
                    continue
                m, hw = ci95(rows.values)
                means.append(m); hws.append(hw)
            ax.errorbar(dens, means, yerr=hws,
                        label=PROTO_LABELS[PROTO_ORDER.index(proto)],
                        color=COLORS[proto], marker=MARKERS[proto],
                        capsize=2, capthick=0.8, elinewidth=0.8)

        ax.set_xlabel("Vehicle Density (nodes)")
        ax.set_ylabel("PDR")
        ax.set_title(title, pad=4)
        ax.set_ylim(0, 1.05)
        ax.set_xticks(dens)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1.0))

    axes[0].legend(loc="lower left", ncol=2, framealpha=0.8)
    fig.tight_layout()
    save(fig, "fig1_pdr_density.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Figure 2 — Deadline Miss Rate vs Density (DENM emergency messages)
# ══════════════════════════════════════════════════════════════════════════════
def fig_deadline_density():
    urban = df[df["scenario"] == "urban"]
    dens  = sorted(urban["n_agents"].unique())

    fig, ax = plt.subplots(figsize=(3.2, 2.5))

    for proto in PROTO_ORDER:
        means, hws = [], []
        for n in dens:
            rows = urban[(urban["protocol"] == proto) & (urban["n_agents"] == n)]["denm_deadline_rate"]
            if len(rows) == 0:
                means.append(np.nan); hws.append(0.0)
                continue
            # deadline_rate = fraction delivered within deadline → miss = 1 - rate
            miss = 1.0 - rows.values
            m, hw = ci95(miss)
            means.append(m); hws.append(hw)
        ax.errorbar(dens, means, yerr=hws,
                    label=PROTO_LABELS[PROTO_ORDER.index(proto)],
                    color=COLORS[proto], marker=MARKERS[proto],
                    capsize=2, capthick=0.8, elinewidth=0.8)

    ax.axhline(0.5, color="red", linewidth=0.8, linestyle="--", label="C-V2X structural\ndeadline (50%)")
    ax.set_xlabel("Vehicle Density (nodes)")
    ax.set_ylabel("DENM Deadline Miss Rate")
    ax.set_ylim(0, 1.0)
    ax.set_xticks(dens)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1.0))
    ax.legend(loc="upper left", ncol=1, framealpha=0.8, fontsize=7)
    ax.set_title("Emergency Message Deadline Miss Rate\n(Urban, 50 ms threshold)", pad=4)
    fig.tight_layout()
    save(fig, "fig2_deadline_density.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Figure 3 — p99 Latency comparison across scenarios
# ══════════════════════════════════════════════════════════════════════════════
def fig_latency_scenarios():
    scenarios = ["urban", "highway", "cascade"]
    scenario_labels = ["Urban\nIntersection", "Highway\nPlatoon", "Cascade\nEmergency"]
    density = 75  # representative density

    fig, ax = plt.subplots(figsize=(3.2, 2.5))

    x = np.arange(len(scenarios))
    n_proto = len(PROTO_ORDER)
    w = 0.12
    offsets = np.linspace(-(n_proto-1)*w/2, (n_proto-1)*w/2, n_proto)

    for i, proto in enumerate(PROTO_ORDER):
        vals, errs = [], []
        for sc in scenarios:
            rows = df[(df["scenario"] == sc) &
                      (df["protocol"] == proto) &
                      (df["n_agents"] == density)]["latency_p99_ms"]
            if len(rows) == 0:
                vals.append(0); errs.append(0)
            else:
                m, hw = ci95(rows.values)
                vals.append(m); errs.append(hw)
        ax.bar(x + offsets[i], vals, w*0.9,
               label=PROTO_LABELS[i],
               color=COLORS[proto], alpha=0.85,
               yerr=errs, capsize=2, error_kw={"elinewidth": 0.7})

    ax.axhline(50, color="red", linewidth=0.8, linestyle="--", label="50 ms deadline")
    ax.set_xticks(x)
    ax.set_xticklabels(scenario_labels)
    ax.set_ylabel("p99 Latency (ms)")
    ax.set_title("p99 Latency by Scenario (N=75)", pad=4)
    ax.legend(loc="upper right", ncol=1, framealpha=0.8, fontsize=6.5)
    fig.tight_layout()
    save(fig, "fig3_latency_scenarios.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Figure 4 — Protocol comparison radar (urban N=75)
# ══════════════════════════════════════════════════════════════════════════════
def fig_radar():
    urban75 = df[(df["scenario"] == "urban") & (df["n_agents"] == 75)]
    metrics = {
        "BSM PDR":        ("bsm_pdr",          True,  1.0),
        "DENM PDR":       ("denm_pdr",          True,  1.0),
        "Deadline\nKeep": ("denm_deadline_rate",True,  1.0),
        "Low\nLatency":   ("latency_p99_ms",    False, 200),
        "Emerg\nLatency": ("emergency_p99_ms",  False, 100),
    }
    metric_names = list(metrics.keys())
    N = len(metric_names)
    angles = [n / N * 2 * np.pi for n in range(N)]
    angles += angles[:1]

    fig, ax = plt.subplots(1, 1, figsize=(3.2, 3.2),
                           subplot_kw={"polar": True})

    for proto in PROTO_ORDER:
        rows = urban75[urban75["protocol"] == proto]
        if len(rows) == 0:
            continue
        vals = []
        for col, higher_better, scale in metrics.values():
            m = rows[col].mean()
            norm = m / scale
            if not higher_better:
                norm = 1.0 - min(norm, 1.0)
            vals.append(np.clip(norm, 0, 1))
        vals += vals[:1]
        ax.plot(angles, vals, color=COLORS[proto],
                marker=MARKERS[proto], markersize=4,
                label=PROTO_LABELS[PROTO_ORDER.index(proto)],
                linewidth=1.2)
        ax.fill(angles, vals, color=COLORS[proto], alpha=0.07)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(metric_names, size=7.5)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], size=6)
    ax.set_ylim(0, 1)
    ax.set_title("Protocol Performance Profile\n(Urban, N=75)", pad=16, size=9)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15),
              ncol=1, framealpha=0.8, fontsize=6.5)
    fig.tight_layout()
    save(fig, "fig4_radar.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Figure 5 — PDR heatmap across scenarios × densities (QDAP vs best competitor)
# ══════════════════════════════════════════════════════════════════════════════
def fig_heatmap_gain():
    scenarios = ["urban", "highway", "cascade"]
    dens      = sorted(df["n_agents"].unique())

    # Build gain matrix: QDAP PDR / best-competitor PDR
    gain = np.zeros((len(scenarios), len(dens)))
    for i, sc in enumerate(scenarios):
        for j, n in enumerate(dens):
            subset = df[(df["scenario"] == sc) & (df["n_agents"] == n)]
            qdap_pdr = subset[subset["protocol"] == "QDAP"]["bsm_pdr"].mean()
            competitors = [p for p in PROTO_ORDER if p != "QDAP"]
            best_comp = max(
                subset[subset["protocol"] == p]["bsm_pdr"].mean()
                for p in competitors if len(subset[subset["protocol"] == p]) > 0
            )
            if best_comp > 0:
                gain[i, j] = (qdap_pdr - best_comp) / best_comp * 100  # % gain
            else:
                gain[i, j] = 0.0

    fig, ax = plt.subplots(figsize=(3.5, 2.0))
    im = ax.imshow(gain, cmap="RdYlGn", vmin=-10, vmax=30, aspect="auto")
    ax.set_xticks(range(len(dens)))
    ax.set_xticklabels([str(n) for n in dens])
    ax.set_yticks(range(len(scenarios)))
    ax.set_yticklabels(["Urban", "Highway", "Cascade"])
    ax.set_xlabel("Vehicle Density (nodes)")
    ax.set_title("QDAP PDR Gain over Best Competitor (%)", pad=4)

    # Annotate cells
    for i in range(len(scenarios)):
        for j in range(len(dens)):
            ax.text(j, i, f"{gain[i,j]:+.1f}%", ha="center", va="center",
                    fontsize=7, color="black" if abs(gain[i,j]) < 20 else "white")

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(labelsize=7)
    cbar.set_label("PDR gain (%)", size=7)
    fig.tight_layout()
    save(fig, "fig5_pdr_gain_heatmap.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Figure 6 — CBR (Channel Busy Ratio) vs Density
# ══════════════════════════════════════════════════════════════════════════════
def fig_cbr_density():
    urban = df[df["scenario"] == "urban"]
    dens  = sorted(urban["n_agents"].unique())

    if "mean_cbr" not in urban.columns:
        print("  Skipping fig6: no mean_cbr column")
        return

    fig, ax = plt.subplots(figsize=(3.2, 2.5))
    for proto in PROTO_ORDER:
        means, hws = [], []
        for n in dens:
            rows = urban[(urban["protocol"] == proto) & (urban["n_agents"] == n)]["mean_cbr"]
            if len(rows) == 0 or rows.isna().all():
                means.append(np.nan); hws.append(0.0)
                continue
            m, hw = ci95(rows.dropna().values)
            means.append(m); hws.append(hw)
        ax.errorbar(dens, means, yerr=hws,
                    label=PROTO_LABELS[PROTO_ORDER.index(proto)],
                    color=COLORS[proto], marker=MARKERS[proto],
                    capsize=2, capthick=0.8, elinewidth=0.8)

    ax.axhline(0.65, color="red", linewidth=0.8, linestyle="--", label="ETSI CBR\nthreshold (65%)")
    ax.set_xlabel("Vehicle Density (nodes)")
    ax.set_ylabel("Mean Channel Busy Ratio")
    ax.set_ylim(0, 1.0)
    ax.set_xticks(dens)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(xmax=1.0))
    ax.set_title("Channel Busy Ratio vs. Vehicle Density\n(Urban Scenario)", pad=4)
    ax.legend(loc="upper left", ncol=1, framealpha=0.8, fontsize=7)
    fig.tight_layout()
    save(fig, "fig6_cbr_density.pdf")

# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import matplotlib.ticker
    print(f"Loaded {len(df)} rows from {CSV_PATH}")
    print(f"Scenarios: {sorted(df['scenario'].unique())}")
    print(f"Densities: {sorted(df['n_agents'].unique())}")
    print(f"Protocols: {sorted(df['protocol'].unique())}")
    print()

    fig_pdr_density()
    fig_deadline_density()
    fig_latency_scenarios()
    fig_radar()
    fig_heatmap_gain()
    fig_cbr_density()

    print(f"\nAll figures saved to: {OUT_DIR}")
