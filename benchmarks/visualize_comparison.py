#!/usr/bin/env python3
"""
Protocol Comparison Visualization
===================================
Phase 11.1 benchmark sonuçlarından paper-quality grafikler.

Üretilen grafikler:
  1. Emergency delivery rate — 8 protokol × 3 senaryo
  2. Latency (p50, p95, p99) — crisis
  3. Throughput — normal
  4. Feature support heatmap
  5. Crisis emergency delivery breakdown
"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

RESULTS_DIR = Path(__file__).parent / "results"
OUT_DIR     = RESULTS_DIR

COLORS = {
    "Raw TCP":      "#475569",
    "HTTP/1.1":     "#64748B",
    "HTTP/2":       "#0891B2",
    "HTTP/3 (QUIC)":"#06B6D4",
    "MQTT 3.1.1":   "#EF4444",
    "MQTT 5.0":     "#F97316",
    "WebSocket":    "#8B5CF6",
    "QDAP":         "#10B981",
}
BG    = "#0D1B2A"
BG2   = "#0F2744"
GRID  = "#1E3A5F"
WHITE = "#FFFFFF"
SLATE = "#94A3B8"


def load_results() -> dict:
    path = RESULTS_DIR / "protocol_comparison.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} bulunamadı. Önce 'python benchmarks/protocol_comparison.py' çalıştır."
        )
    with open(path) as f:
        return json.load(f)


def setup_ax(ax, title: str):
    ax.set_facecolor(BG2)
    ax.tick_params(colors=SLATE, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(axis='y', color=GRID, linewidth=0.5, alpha=0.7)
    ax.set_title(title, color=WHITE, fontsize=11, fontweight='bold', pad=8)


def _proto_name_match(name: str) -> str:
    """Normalize protocol names for color lookup."""
    for key in COLORS:
        if key.lower() in name.lower() or name.lower() in key.lower():
            return key
    return name


def plot_emergency_comparison(data: dict, ax):
    scenarios = ["normal", "challenged", "crisis"]
    s_labels  = ["Normal\n(20ms/1%)", "Challenged\n(100ms/5%)", "Crisis\n(300ms/35%)"]
    protocols = list(COLORS.keys())

    x = np.arange(len(scenarios))
    n = len(protocols)
    w = 0.09
    offset = np.linspace(-(n // 2) * w, (n // 2) * w, n)

    for i, proto in enumerate(protocols):
        vals = []
        for sc in scenarios:
            sc_data = data["results"].get(sc, [])
            r = next((r for r in sc_data if _proto_name_match(r.get("name","")) == proto), None)
            vals.append(r.get("emrg_delivery_rate", 0) if r else 0)

        bar = ax.bar(
            x + offset[i], vals, w,
            color=COLORS[proto],
            alpha=0.9 if proto == "QDAP" else 0.65,
            label=proto, zorder=3,
        )
        if proto == "QDAP":
            for rect, v in zip(bar, vals):
                if v > 0:
                    ax.text(
                        rect.get_x() + rect.get_width() / 2,
                        v + 1.5, f"{v:.0f}%",
                        ha='center', va='bottom',
                        fontsize=7, color=COLORS["QDAP"], fontweight='bold',
                    )

    ax.set_xticks(x)
    ax.set_xticklabels(s_labels, color=SLATE, fontsize=9)
    ax.set_ylabel("Emergency Delivery Rate (%)", color=SLATE, fontsize=9)
    ax.set_ylim(0, 115)
    ax.axhline(100, color=GRID, linewidth=0.5, linestyle=":")
    setup_ax(ax, "Emergency Delivery Rate — 8 Protocol x 3 Scenario")


def plot_latency_comparison(data: dict, ax):
    protocols = list(COLORS.keys())
    crisis = data["results"].get("crisis", [])

    p50s, p95s, p99s, labels = [], [], [], []
    for proto in protocols:
        r = next((r for r in crisis if _proto_name_match(r.get("name","")) == proto), None)
        if r:
            p50s.append(r.get("latency_p50_ms", 0))
            p95s.append(r.get("latency_p95_ms", 0))
            p99s.append(r.get("latency_p99_ms", 0))
            labels.append(proto)

    x = np.arange(len(labels))
    w = 0.25
    colors = [COLORS[l] for l in labels]

    ax.bar(x - w, p50s, w, label="p50", alpha=0.9, color=colors, zorder=3)
    ax.bar(x,     p95s, w, label="p95", alpha=0.6, color=colors, zorder=3)
    ax.bar(x + w, p99s, w, label="p99", alpha=0.3, color=colors, zorder=3)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right', color=SLATE, fontsize=8)
    ax.set_ylabel("Latency (ms)", color=SLATE, fontsize=9)
    setup_ax(ax, "Latency p50/p95/p99 — Crisis Scenario")

    from matplotlib.patches import Patch
    ax.legend(
        handles=[Patch(color='white', alpha=a, label=l)
                 for a, l in [(0.9, "p50"), (0.6, "p95"), (0.3, "p99")]],
        facecolor=BG, labelcolor=WHITE, fontsize=8, loc='upper left',
    )


def plot_throughput(data: dict, ax):
    protocols = list(COLORS.keys())
    normal = data["results"].get("normal", [])

    vals, labels = [], []
    for proto in protocols:
        r = next((r for r in normal if _proto_name_match(r.get("name","")) == proto), None)
        if r:
            vals.append(r.get("throughput_mbps", 0))
            labels.append(proto)

    colors = [COLORS[l] for l in labels]
    y = np.arange(len(labels))
    bars = ax.barh(y, vals, color=colors, alpha=0.85, zorder=3)

    for bar, v, proto in zip(bars, vals, labels):
        ax.text(
            v + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{v:.1f}", va='center', ha='left', fontsize=8,
            color=COLORS[proto],
            fontweight='bold' if proto == "QDAP" else 'normal',
        )

    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=SLATE, fontsize=9)
    ax.set_xlabel("Throughput (Mbps)", color=SLATE, fontsize=9)
    setup_ax(ax, "Throughput — Normal Scenario")


def plot_contribution_heatmap(ax):
    protocols = ["HTTP/1.1", "HTTP/2", "HTTP/3", "MQTT 3.1", "MQTT 5.0", "WebSocket", "QDAP"]
    features  = [
        "QFT Scheduling", "Frame Priority", "Convergence Proof",
        "Zero Keepalive", "F1 Bound", "Built-in Security",
        "Emergency Override", "Cross-layer",
    ]
    matrix = np.array([
        [0, 0, 0, 0, 0, 0, 2],
        [0, 1, 1, 0, 0, 0, 2],
        [0, 0, 0, 0, 0, 0, 2],
        [0, 0, 0, 1, 1, 0, 2],
        [0, 0, 0, 0, 0, 0, 2],
        [0, 0, 2, 0, 0, 0, 2],
        [0, 0, 0, 0, 0, 0, 2],
        [0, 0, 0, 0, 0, 0, 2],
    ])

    cmap = matplotlib.colors.ListedColormap([GRID, "#F59E0B", "#10B981"])
    ax.imshow(matrix, cmap=cmap, vmin=0, vmax=2, aspect='auto')

    ax.set_xticks(range(len(protocols)))
    ax.set_xticklabels(protocols, rotation=30, ha='right', color=SLATE, fontsize=8)
    ax.set_yticks(range(len(features)))
    ax.set_yticklabels(features, color=SLATE, fontsize=9)

    for i in range(len(features)):
        for j in range(len(protocols)):
            v = matrix[i, j]
            sym   = "✓" if v == 2 else "~" if v == 1 else "✗"
            color = "#10B981" if v == 2 else "#F59E0B" if v == 1 else SLATE
            ax.text(j, i, sym, ha='center', va='center',
                    fontsize=11, color=color, fontweight='bold')

    setup_ax(ax, "Feature Support Matrix")
    ax.set_facecolor(BG2)

    from matplotlib.patches import Patch
    ax.legend(
        handles=[
            Patch(color="#10B981", label="Full support"),
            Patch(color="#F59E0B", label="Partial"),
            Patch(color=GRID, label="Not supported"),
        ],
        facecolor=BG, labelcolor=WHITE, fontsize=8,
        loc='lower right', bbox_to_anchor=(1.0, -0.3),
    )


def plot_crisis_breakdown(data: dict, ax):
    protocols = list(COLORS.keys())
    crisis = data["results"].get("crisis", [])

    vals, labels = [], []
    for proto in protocols:
        r = next((r for r in crisis if _proto_name_match(r.get("name","")) == proto), None)
        if r:
            vals.append(r.get("emrg_delivery_rate", 0))
            labels.append(proto)

    colors = [COLORS[l] for l in labels]
    y = np.arange(len(labels))
    bars = ax.barh(y, vals, color=colors, alpha=0.85, zorder=3)
    ax.axvline(x=50, color="#F59E0B", linewidth=1, linestyle="--", alpha=0.5)

    for bar, v, proto in zip(bars, vals, labels):
        ax.text(
            v + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{v:.0f}%", va='center', ha='left', fontsize=9,
            color=COLORS[proto],
            fontweight='bold' if proto == "QDAP" else 'normal',
        )

    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=SLATE, fontsize=9)
    ax.set_xlabel("Emergency Delivery Rate (%)", color=SLATE, fontsize=9)
    ax.set_xlim(0, 120)
    setup_ax(ax, "Crisis Emergency Delivery (300ms RTT, 35% Loss)")


def main():
    print("Protocol Comparison Visualization...")
    data = load_results()

    fig = plt.figure(figsize=(20, 16))
    fig.patch.set_facecolor(BG)

    from matplotlib.gridspec import GridSpec
    gs = GridSpec(3, 2, figure=fig, hspace=0.55, wspace=0.35)

    ax1 = fig.add_subplot(gs[0, :])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[1, 1])
    ax4 = fig.add_subplot(gs[2, 0])
    ax5 = fig.add_subplot(gs[2, 1])

    plot_emergency_comparison(data, ax1)
    plot_latency_comparison(data, ax2)
    plot_throughput(data, ax3)
    plot_contribution_heatmap(ax4)
    plot_crisis_breakdown(data, ax5)

    handles = [mpatches.Patch(color=c, label=p) for p, c in COLORS.items()]
    ax1.legend(
        handles=handles, facecolor=BG, labelcolor=WHITE,
        fontsize=8, ncol=4, loc='lower center', bbox_to_anchor=(0.5, -0.22),
    )

    fig.suptitle(
        "QDAP vs Application-Layer Protocols — Comprehensive Comparison\n"
        "8 Protocols × 3 Network Scenarios",
        color=WHITE, fontsize=14, fontweight='bold', y=0.98,
    )

    out = OUT_DIR / "protocol_comparison.png"
    plt.savefig(out, dpi=180, bbox_inches='tight', facecolor=BG, edgecolor='none')
    plt.close()
    print(f"✅ Kaydedildi: {out}")


if __name__ == "__main__":
    main()
