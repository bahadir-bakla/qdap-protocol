"""
Publication-quality plots for the QDAP V2X benchmark.

Output: simulations/v2x/results/v2x_benchmark.pdf  (6 pages)

Page 1 — Emergency DENM PDR vs agent density   (urban + highway)
Page 2 — BSM PDR vs agent density              (urban + highway)
Page 3 — Emergency latency CDF at 80 agents   (urban + highway)
Page 4 — Safety metric: DENM within 50 ms     (urban + highway)
Page 5 — Cascade reaction-time box plot        (highway, 80 vehicles)
Page 6 — Radar / spider chart                  (urban, 80 agents)
"""
import os
from typing import Dict, List

import numpy as np

import matplotlib
matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_pdf import PdfPages

from simulation import SimMetrics

# ─────────────────────────────────────────────────────────────────────────────
# Shared style constants
# ─────────────────────────────────────────────────────────────────────────────

PROTOCOL_COLORS = {
    "QDAP":          "#06D6A0",
    "DSRC 802.11p":  "#E63946",
    "802.11bd":      "#F4A261",
    "C-V2X Mode 4":  "#2A9D8F",
    "UDP":           "#457B9D",
    "MQTT":          "#8338EC",
}

PROTOCOL_MARKERS = {
    "QDAP":          "o",
    "DSRC 802.11p":  "s",
    "802.11bd":      "^",
    "C-V2X Mode 4":  "D",
    "UDP":           "v",
    "MQTT":          "x",
}

# Protocol display order (QDAP first for visual prominence)
PROTO_ORDER = [
    "QDAP", "DSRC 802.11p", "802.11bd", "C-V2X Mode 4", "UDP", "MQTT"
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _style_ax(ax, title: str = "", xlabel: str = "", ylabel: str = ""):
    """Apply uniform publication style to an axes object."""
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.tick_params(labelsize=8)
    ax.grid(True, alpha=0.30, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _best_run_per_proto(runs: List[SimMetrics]) -> List[SimMetrics]:
    """Return the first run found for each unique protocol name."""
    seen: Dict[str, SimMetrics] = {}
    for r in runs:
        if r.protocol_name not in seen:
            seen[r.protocol_name] = r
    # Return in canonical order, falling back to insertion order
    result = []
    for name in PROTO_ORDER:
        if name in seen:
            result.append(seen[name])
    for name, r in seen.items():
        if name not in PROTO_ORDER:
            result.append(r)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Figure 1 / 2 — PDR vs vehicle density
# ─────────────────────────────────────────────────────────────────────────────

def plot_pdr_vs_density(
    ax,
    results_by_density: Dict[int, List[SimMetrics]],
    scenario: str,
    msg_type: str = "denm",
):
    """
    Line plot of mean ± std PDR for all protocols across density sweep.

    Parameters
    ----------
    msg_type : "denm" | "bsm"
    """
    densities = sorted(results_by_density.keys())

    for proto_name in PROTO_ORDER:
        color = PROTOCOL_COLORS.get(proto_name, "gray")
        marker = PROTOCOL_MARKERS.get(proto_name, "o")

        pdrs, stds = [], []
        for n in densities:
            runs = [
                r for r in results_by_density[n]
                if r.protocol_name == proto_name
            ]
            if not runs:
                pdrs.append(0.0)
                stds.append(0.0)
                continue
            vals = [
                r.denm_pdr if msg_type == "denm" else r.bsm_pdr
                for r in runs
            ]
            pdrs.append(float(np.mean(vals)))
            stds.append(float(np.std(vals)))

        pdrs_arr = np.array(pdrs) * 100.0
        stds_arr = np.array(stds) * 100.0

        lw = 2.5 if proto_name == "QDAP" else 1.8
        ms = 6 if proto_name == "QDAP" else 5
        ax.plot(
            densities, pdrs_arr,
            color=color, marker=marker,
            linewidth=lw, markersize=ms,
            label=proto_name,
            zorder=3 if proto_name == "QDAP" else 2,
        )
        ax.fill_between(
            densities,
            pdrs_arr - stds_arr,
            pdrs_arr + stds_arr,
            alpha=0.12, color=color,
        )

    msg_label = "Emergency DENM" if msg_type == "denm" else "BSM"
    _style_ax(
        ax,
        title=f"{msg_label} Delivery Rate — {scenario.title()} Scenario",
        xlabel="Number of Traffic Agents",
        ylabel="Packet Delivery Rate (%)",
    )
    ax.set_ylim(0, 108)
    ax.axhline(y=90, color="gray", linestyle=":", alpha=0.55,
               label="90% threshold", linewidth=1)
    ax.legend(fontsize=7, loc="lower left", framealpha=0.90, ncol=2)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 3 — Latency CDF
# ─────────────────────────────────────────────────────────────────────────────

def plot_latency_cdf(
    ax,
    results_at_n: List[SimMetrics],
    msg_type: str = "emergency",
):
    """
    Empirical CDF of one-way latency.
    results_at_n : one SimMetrics per protocol (already filtered to one run).
    """
    for run in results_at_n:
        lats = (
            run.latencies_emergency_ms
            if msg_type == "emergency"
            else run.latencies_normal_ms
        )
        if not lats:
            continue

        lats_sorted = np.sort(lats)
        cdf = np.arange(1, len(lats_sorted) + 1) / len(lats_sorted) * 100.0
        color = PROTOCOL_COLORS.get(run.protocol_name, "gray")
        lw = 2.5 if run.protocol_name == "QDAP" else 1.8
        ax.plot(
            lats_sorted, cdf,
            color=color, linewidth=lw,
            label=run.protocol_name,
            zorder=3 if run.protocol_name == "QDAP" else 2,
        )

    label = "Emergency DENM" if msg_type == "emergency" else "BSM"
    _style_ax(
        ax,
        title=f"{label} Latency CDF (80 Agents)",
        xlabel="One-way Latency (ms)",
        ylabel="Cumulative Probability (%)",
    )
    ax.axvline(x=50,  color="red",    linestyle="--", alpha=0.65,
               label="50 ms deadline (DENM)", linewidth=1.2)
    ax.axvline(x=100, color="orange", linestyle="--", alpha=0.65,
               label="100 ms deadline (BSM)", linewidth=1.2)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 105)
    ax.legend(fontsize=7, loc="lower right", framealpha=0.90)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 5 — Cascade reaction time (box plot)
# ─────────────────────────────────────────────────────────────────────────────

def plot_cascade_times(ax, results: Dict[str, List[float]]):
    """
    Box plot of time-to-first-DENM-receipt per downstream vehicle.
    results : {protocol_name: [reaction_time_ms, ...]}
    """
    # Use canonical ordering
    names = [n for n in PROTO_ORDER if n in results and results[n]]
    data = [results[n] for n in names]
    colors = [PROTOCOL_COLORS.get(n, "gray") for n in names]

    if not data:
        ax.text(0.5, 0.5, "No cascade data collected",
                transform=ax.transAxes, ha="center", va="center", fontsize=10)
        _style_ax(ax, title="Cascade Reaction Time")
        return

    bp = ax.boxplot(
        data,
        patch_artist=True,
        notch=False,
        medianprops=dict(color="black", linewidth=2),
        flierprops=dict(marker=".", markersize=3, alpha=0.5),
    )
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.70)

    ax.set_xticks(range(1, len(names) + 1))
    ax.set_xticklabels(names, rotation=18, ha="right", fontsize=7)
    ax.axhline(y=100, color="red", linestyle="--", alpha=0.65,
               label="100 ms reaction budget", linewidth=1.2)
    _style_ax(
        ax,
        title="Emergency Cascade Reaction Time — Highway (80 Vehicles)",
        xlabel="Protocol",
        ylabel="Time to Receive First DENM (ms)",
    )
    ax.legend(fontsize=7)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 4 — Safety metric
# ─────────────────────────────────────────────────────────────────────────────

def plot_safety_metric(
    ax,
    results_by_density: Dict[int, List[SimMetrics]],
    scenario: str,
):
    """
    Fraction of vehicles that receive the DENM within the 50 ms deadline.
    This is the primary V2X safety KPI.
    """
    densities = sorted(results_by_density.keys())

    for proto_name in PROTO_ORDER:
        color = PROTOCOL_COLORS.get(proto_name, "gray")
        marker = PROTOCOL_MARKERS.get(proto_name, "o")

        rates, stds = [], []
        for n in densities:
            runs = [
                r for r in results_by_density[n]
                if r.protocol_name == proto_name
            ]
            vals = [r.denm_deadline_rate for r in runs] if runs else [0.0]
            rates.append(float(np.mean(vals)) * 100.0)
            stds.append(float(np.std(vals)) * 100.0)

        rates_arr = np.array(rates)
        stds_arr = np.array(stds)
        lw = 2.5 if proto_name == "QDAP" else 1.8
        ms = 6 if proto_name == "QDAP" else 5
        ax.plot(
            densities, rates_arr,
            color=color, marker=marker,
            linewidth=lw, markersize=ms,
            label=proto_name,
            zorder=3 if proto_name == "QDAP" else 2,
        )
        ax.fill_between(
            densities,
            rates_arr - stds_arr,
            rates_arr + stds_arr,
            alpha=0.12, color=color,
        )

    _style_ax(
        ax,
        title=f"Safety: DENM Delivery Within 50 ms — {scenario.title()}",
        xlabel="Number of Traffic Agents",
        ylabel="Vehicles Notified Within 50 ms (%)",
    )
    ax.set_ylim(0, 108)
    ax.axhline(y=95, color="darkred", linestyle=":", alpha=0.55,
               label="95% safety target", linewidth=1)
    ax.legend(fontsize=7, loc="lower left", framealpha=0.90, ncol=2)


# ─────────────────────────────────────────────────────────────────────────────
# Figure 6 — Radar / spider chart
# ─────────────────────────────────────────────────────────────────────────────

def plot_radar(ax, summary: Dict[str, List[float]]):
    """
    Radar chart comparing protocols on five normalised dimensions.
    summary : {proto_name: [score_0..score_4]} — values in [0, 1].
    Dimensions: BSM delivery, DENM delivery, low latency, high-density, safety.
    """
    categories = [
        "BSM\nDelivery",
        "DENM\nDelivery",
        "Low\nLatency",
        "High\nDensity",
        "Safety\nMargin",
    ]
    N = len(categories)
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]  # close the polygon

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, size=8)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.50, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], size=6)
    ax.grid(True, alpha=0.40)

    for proto_name in PROTO_ORDER:
        scores = summary.get(proto_name)
        if scores is None:
            continue
        color = PROTOCOL_COLORS.get(proto_name, "gray")
        vals = list(scores) + [scores[0]]
        lw = 2.5 if proto_name == "QDAP" else 1.8
        ax.plot(angles, vals, color=color, linewidth=lw, label=proto_name)
        ax.fill(angles, vals, color=color,
                alpha=0.12 if proto_name == "QDAP" else 0.05)

    ax.set_title(
        "Protocol Comparison Radar\n(Urban Intersection, 80 Agents)",
        fontsize=10, fontweight="bold", pad=18,
    )
    ax.legend(
        fontsize=7, loc="upper right",
        bbox_to_anchor=(1.38, 1.15),
        framealpha=0.90,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Top-level PDF generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(all_results: Dict, output_path: str):
    """
    Generate the complete benchmark PDF.

    Parameters
    ----------
    all_results : {
        "urban":   {n_agents: [SimMetrics, ...], ...},
        "highway": {...},
        "cascade": {...},
    }
    output_path : absolute path for the output PDF.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with PdfPages(output_path) as pdf:

        # ── Page 1: Emergency DENM PDR vs density ─────────────────────
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        fig.suptitle(
            "QDAP V2X Benchmark: Emergency DENM Delivery Rate vs Agent Density",
            fontsize=13, fontweight="bold",
        )
        plot_pdr_vs_density(axes[0], all_results["urban"],   "Urban Intersection", "denm")
        plot_pdr_vs_density(axes[1], all_results["highway"], "Highway Platoon",    "denm")
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Page 2: BSM PDR vs density ────────────────────────────────
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        fig.suptitle(
            "QDAP V2X Benchmark: BSM Delivery Rate vs Agent Density",
            fontsize=13, fontweight="bold",
        )
        plot_pdr_vs_density(axes[0], all_results["urban"],   "Urban Intersection", "bsm")
        plot_pdr_vs_density(axes[1], all_results["highway"], "Highway Platoon",    "bsm")
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Page 3: Emergency latency CDF @ 80 agents ─────────────────
        n80_urban   = all_results["urban"].get(80, [])
        n80_highway = all_results["highway"].get(80, [])

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        fig.suptitle(
            "Emergency DENM Latency CDF — 80 Traffic Agents",
            fontsize=13, fontweight="bold",
        )
        plot_latency_cdf(axes[0], _best_run_per_proto(n80_urban),   "emergency")
        plot_latency_cdf(axes[1], _best_run_per_proto(n80_highway),  "emergency")
        axes[0].set_title("Urban Intersection", fontsize=10, fontweight="bold")
        axes[1].set_title("Highway Platoon",    fontsize=10, fontweight="bold")
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Page 4: Safety metric ─────────────────────────────────────
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        fig.suptitle(
            "Safety Metric: DENM Within 50 ms Deadline",
            fontsize=13, fontweight="bold",
        )
        plot_safety_metric(axes[0], all_results["urban"],   "urban")
        plot_safety_metric(axes[1], all_results["highway"], "highway")
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Page 5: Cascade reaction-time box plot ────────────────────
        cascade_results = all_results.get("cascade", {})
        n80_cascade = cascade_results.get(80, [])
        cascade_by_proto: Dict[str, List[float]] = {}
        for r in n80_cascade:
            cascade_by_proto.setdefault(r.protocol_name, []).extend(
                r.cascade_times_ms
            )

        fig, ax = plt.subplots(1, 1, figsize=(10, 5))
        fig.suptitle(
            "Emergency Cascade Reaction Time — Highway Platoon (80 Vehicles)",
            fontsize=13, fontweight="bold",
        )
        plot_cascade_times(ax, cascade_by_proto)
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Page 6: Radar chart ───────────────────────────────────────
        n80_urban_runs = _best_run_per_proto(all_results["urban"].get(80, []))
        radar_data: Dict[str, List[float]] = {}
        for r in n80_urban_runs:
            # Normalise each dimension to [0, 1]
            lat_score    = 1.0 / (1.0 + r.latency_p99 / 50.0)
            density_score = (r.bsm_pdr * r.denm_pdr) ** 0.5  # geometric mean
            radar_data[r.protocol_name] = [
                r.bsm_pdr,           # BSM delivery
                r.denm_pdr,          # DENM delivery
                lat_score,           # low latency (inverted p99)
                density_score,       # high-density combined PDR
                r.denm_deadline_rate,# safety: fraction within 50 ms
            ]

        fig, ax = plt.subplots(
            1, 1, figsize=(8, 8),
            subplot_kw=dict(projection="polar"),
        )
        fig.suptitle(
            "Protocol Multi-Dimensional Comparison",
            fontsize=13, fontweight="bold",
        )
        if radar_data:
            plot_radar(ax, radar_data)
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── PDF metadata ──────────────────────────────────────────────
        d = pdf.infodict()
        d["Title"]   = "QDAP V2X Protocol Benchmark"
        d["Author"]  = "Bahadir Bakla — QDAP Research"
        d["Subject"] = (
            "V2V/V2X Protocol Comparison: "
            "QDAP vs DSRC/802.11bd/C-V2X Mode 4/UDP/MQTT"
        )
        d["Keywords"] = "V2X QDAP DSRC C-V2X 802.11bd autonomous vehicles"

    print(f"[plots] Saved: {output_path}")
