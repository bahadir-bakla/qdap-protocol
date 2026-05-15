#!/usr/bin/env python3
"""
QDAP V2X Research Report Generator
====================================
Generates a single combined PDF: technical brief + all benchmark figures.
Suitable for Tesla / Waymo / IEEE / 5GAA submission.

Usage:
    python generate_report.py
    python generate_report.py --output my_report.pdf
"""
import os
import sys
import csv
import io
import argparse
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CSV_PATH = os.path.join(RESULTS_DIR, "v2x_results.csv")

# ── Brand colors ──────────────────────────────────────────────────────────────
QDAP_GREEN    = "#06D6A0"
DSRC_RED      = "#E63946"
BD_ORANGE     = "#F4A261"
CV2X_TEAL     = "#2A9D8F"
UDP_BLUE      = "#457B9D"
MQTT_PURPLE   = "#8338EC"
BG_DARK       = "#0D1117"
BG_CARD       = "#161B22"
TEXT_PRIMARY  = "#E6EDF3"
TEXT_DIM      = "#8B949E"
ACCENT        = "#06D6A0"

PROTO_ORDER  = ["QDAP", "802.11bd", "C-V2X Mode 4", "DSRC 802.11p", "UDP", "MQTT"]
PROTO_COLORS = {
    "QDAP": QDAP_GREEN, "DSRC 802.11p": DSRC_RED, "802.11bd": BD_ORANGE,
    "C-V2X Mode 4": CV2X_TEAL, "UDP": UDP_BLUE, "MQTT": MQTT_PURPLE,
}
PROTO_LABELS = {
    "QDAP": "QDAP", "DSRC 802.11p": "DSRC\n802.11p", "802.11bd": "802.11bd",
    "C-V2X Mode 4": "C-V2X\nMode 4", "UDP": "UDP", "MQTT": "MQTT",
}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_csv():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"Run the benchmark first: {CSV_PATH}")
    rows = list(csv.DictReader(open(CSV_PATH)))
    return rows


def agg(rows, scenario, n, proto):
    runs = [r for r in rows if r["scenario"] == scenario
            and int(r["n_agents"]) == n and r["protocol"] == proto]
    if not runs:
        return None
    return {
        "denm_pdr": np.mean([float(r["denm_pdr"]) for r in runs]) * 100,
        "denm_ddl": np.mean([float(r["denm_deadline_rate"]) for r in runs]) * 100,
        "bsm_pdr":  np.mean([float(r["bsm_pdr"]) for r in runs]) * 100,
        "p99_e":    np.mean([float(r["emergency_p99_ms"]) for r in runs]),
        "cbr":      np.mean([float(r["mean_cbr"]) for r in runs]),
    }


# ── Shared style helpers ───────────────────────────────────────────────────────

def dark_fig(w=11, h=8.5):
    fig = plt.figure(figsize=(w, h), facecolor=BG_DARK)
    return fig


def style_ax(ax, title="", xlabel="", ylabel=""):
    ax.set_facecolor(BG_CARD)
    for spine in ax.spines.values():
        spine.set_color("#30363D")
    ax.tick_params(colors=TEXT_DIM, labelsize=8)
    ax.xaxis.label.set_color(TEXT_DIM)
    ax.yaxis.label.set_color(TEXT_DIM)
    if title:
        ax.set_title(title, color=TEXT_PRIMARY, fontsize=10, fontweight="bold", pad=8)
    if xlabel:
        ax.set_xlabel(xlabel, color=TEXT_DIM, fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=TEXT_DIM, fontsize=8)
    ax.grid(axis="y", color="#30363D", linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1 — Cover
# ─────────────────────────────────────────────────────────────────────────────

def page_cover(pdf, rows):
    fig = dark_fig(11, 8.5)

    # Header strip
    header = fig.add_axes([0, 0.82, 1, 0.18])
    header.set_facecolor(BG_CARD)
    header.set_xlim(0, 1); header.set_ylim(0, 1)
    header.axis("off")
    header.add_patch(plt.Rectangle((0, 0), 0.006, 1, color=QDAP_GREEN, transform=header.transAxes))
    header.text(0.03, 0.65, "QDAP", color=QDAP_GREEN, fontsize=42,
                fontweight="bold", va="center", fontfamily="monospace")
    header.text(0.03, 0.28, "Emergency-Priority Protocol for V2X and High-Loss Networks",
                color=TEXT_PRIMARY, fontsize=14, va="center")
    header.text(0.97, 0.65, "Research Technical Brief", color=TEXT_DIM, fontsize=10,
                ha="right", va="center")
    header.text(0.97, 0.28, "Bahadir Bakla · bahadirbakla@gmail.com · qdap.dev",
                color=TEXT_DIM, fontsize=8, ha="right", va="center")

    # Headline metrics — 4 big numbers
    metrics = [
        ("99.0%", "Emergency delivery\nat 30% WAN loss", QDAP_GREEN),
        ("+40%", "More DENMs delivered\nvs DSRC 802.11p", BD_ORANGE),
        ("88×", "Lower p99 latency\nvs MQTT", DSRC_RED),
        ("2.8 ms", "Emergency p99\nacross all densities", CV2X_TEAL),
    ]
    for idx, (val, label, color) in enumerate(metrics):
        x0 = 0.02 + idx * 0.245
        ax = fig.add_axes([x0, 0.57, 0.22, 0.22])
        ax.set_facecolor(BG_CARD)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
                                    boxstyle="round,pad=0.02",
                                    facecolor=BG_CARD, edgecolor=color,
                                    linewidth=1.5, transform=ax.transAxes))
        ax.text(0.5, 0.62, val, color=color, fontsize=24, fontweight="bold",
                ha="center", va="center", fontfamily="monospace")
        ax.text(0.5, 0.25, label, color=TEXT_DIM, fontsize=7.5, ha="center",
                va="center", multialignment="center")

    # Key results table (Urban, N=75, 5-run MC)
    n_ref = 75
    table_ax = fig.add_axes([0.02, 0.08, 0.96, 0.46])
    table_ax.set_facecolor(BG_CARD)
    table_ax.set_xlim(0, 1); table_ax.set_ylim(0, 1); table_ax.axis("off")

    table_ax.text(0.01, 0.96, "Urban Intersection Results  (N = 75 agents, 5-run Monte Carlo, seed = 42)",
                  color=TEXT_PRIMARY, fontsize=9, fontweight="bold", va="top")
    table_ax.text(0.01, 0.90, "Channel: Two-ray LOS + WINNER+ B1 NLOS · CBR ≈ 0.36 · DENM deadline: 50 ms",
                  color=TEXT_DIM, fontsize=7.5, va="top")

    cols   = ["Protocol", "DENM PDR", "DENM < 50 ms", "BSM PDR", "Emerg. p99"]
    colx   = [0.01, 0.26, 0.42, 0.58, 0.74]
    row_h  = 0.10
    header_y = 0.80
    for ci, (c, cx) in enumerate(zip(cols, colx)):
        table_ax.text(cx, header_y, c, color=TEXT_DIM, fontsize=7.5, va="top",
                      fontweight="bold")

    table_ax.add_patch(plt.Rectangle((0, header_y - 0.03), 1, 0.002,
                                      color="#30363D", transform=table_ax.transAxes))

    row_data = []
    for p in PROTO_ORDER:
        a = agg(rows, "urban", n_ref, p)
        if a:
            row_data.append((p, a["denm_pdr"], a["denm_ddl"], a["bsm_pdr"], a["p99_e"]))

    for ri, (proto, dpdr, dddl, bpdr, ep99) in enumerate(row_data):
        y = header_y - 0.03 - (ri + 1) * row_h
        color = PROTO_COLORS.get(proto, TEXT_DIM)
        is_qdap = proto == "QDAP"
        bg = "#1C2128" if ri % 2 == 0 else BG_CARD
        table_ax.add_patch(plt.Rectangle((0, y - 0.01), 1, row_h,
                                          color=bg, transform=table_ax.transAxes, zorder=0))
        if is_qdap:
            table_ax.add_patch(plt.Rectangle((0, y - 0.01), 0.004, row_h,
                                              color=QDAP_GREEN, transform=table_ax.transAxes))
        lbl = f"[*] {proto}" if is_qdap else f"    {proto}"
        fw = "bold" if is_qdap else "normal"
        table_ax.text(colx[0], y + 0.025, lbl, color=color, fontsize=8,
                      va="center", fontweight=fw)
        table_ax.text(colx[1], y + 0.025, f"{dpdr:.1f}%", color=color, fontsize=8,
                      va="center", fontweight=fw)
        # Red flag for C-V2X deadline miss
        ddl_color = DSRC_RED if (dddl < dpdr - 5) else color
        ddl_flag = " [MISS]" if (dddl < dpdr - 5) else ""
        table_ax.text(colx[2], y + 0.025, f"{dddl:.1f}%{ddl_flag}", color=ddl_color,
                      fontsize=8, va="center", fontweight=fw)
        table_ax.text(colx[3], y + 0.025, f"{bpdr:.1f}%", color=color, fontsize=8,
                      va="center", fontweight=fw)
        p99_color = DSRC_RED if ep99 > 50 else color
        table_ax.text(colx[4], y + 0.025, f"{ep99:.1f} ms", color=p99_color,
                      fontsize=8, va="center", fontweight=fw)

    table_ax.text(0.5, 0.01,
                  "[*] QDAP  |  [MISS] = Deadline miss  |  Source: simulations/v2x/results/v2x_results.csv",
                  color=TEXT_DIM, fontsize=6.5, ha="center", va="bottom", style="italic")

    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2 — Problem + Architecture
# ─────────────────────────────────────────────────────────────────────────────

def page_architecture(pdf):
    fig = dark_fig(11, 8.5)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_DARK); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # Left column: Problem
    ax.text(0.02, 0.96, "1. The Problem", color=QDAP_GREEN, fontsize=13,
            fontweight="bold", va="top")
    ax.add_patch(plt.Rectangle((0.02, 0.935), 0.44, 0.002, color=QDAP_GREEN))

    problems = [
        ("HTTP/1.1 & HTTP/2",
         "Head-of-line blocking — retransmit lost packets, stall new ones.\n"
         "At 30% WAN loss: only 68.8% of messages meet 500 ms deadline."),
        ("MQTT",
         "Broker-mediated with equal priority. Emergency alerts queue behind\n"
         "telemetry. TCP RTO ~200 ms. p99 reaches 247 ms in V2X."),
        ("DSRC / 802.11p",
         "No application-layer priority. Collision-warning DENM waits behind\n"
         "routine BSMs. 15% collision probability at CBR = 0.50."),
        ("C-V2X Mode 4",
         "Semi-Persistent Scheduling adds 0–100 ms per transmission regardless\n"
         "of urgency. 67% of DENMs miss the ETSI 50 ms safety deadline."),
    ]

    y = 0.90
    for title, desc in problems:
        ax.add_patch(FancyBboxPatch((0.02, y - 0.095), 0.44, 0.088,
                                    boxstyle="round,pad=0.01",
                                    facecolor=BG_CARD, edgecolor="#30363D",
                                    linewidth=0.8))
        ax.add_patch(plt.Rectangle((0.02, y - 0.095), 0.004, 0.088,
                                   color=DSRC_RED))
        ax.text(0.035, y - 0.012, title, color=DSRC_RED, fontsize=8.5,
                fontweight="bold", va="top")
        ax.text(0.035, y - 0.038, desc, color=TEXT_DIM, fontsize=7.2,
                va="top", linespacing=1.5)
        y -= 0.105

    # Right column: Architecture
    ax.text(0.52, 0.96, "2. QDAP Architecture", color=QDAP_GREEN, fontsize=13,
            fontweight="bold", va="top")
    ax.add_patch(plt.Rectangle((0.52, 0.935), 0.46, 0.002, color=QDAP_GREEN))

    components = [
        ("QFT Scheduler", QDAP_GREEN,
         "Emergency messages jump the queue via log-linear softmax priority.\n"
         "374,000 scheduling decisions/sec. Zero head-of-line blocking."),
        ("Adaptive FEC", BD_ORANGE,
         "Observes real-time channel loss per source. Emergency traffic:\n"
         "up to 4× coded redundancy. P(fail) = per^k, fire-and-forget."),
        ("Ghost Session", CV2X_TEAL,
         "0-RTT reconnection after link drop. No TCP handshake overhead.\n"
         "Critical for vehicles moving at 130 km/h between RSUs."),
        ("Delta Encoder", UDP_BLUE,
         "Position updates transmitted as tiny deltas vs last known state.\n"
         "74.4% BSM size reduction. Effective PHY throughput: 20 Mbps."),
    ]

    y = 0.90
    for name, color, desc in components:
        ax.add_patch(FancyBboxPatch((0.52, y - 0.095), 0.46, 0.088,
                                    boxstyle="round,pad=0.01",
                                    facecolor=BG_CARD, edgecolor=color,
                                    linewidth=0.8))
        ax.add_patch(plt.Rectangle((0.52, y - 0.095), 0.004, 0.088,
                                   color=color))
        ax.text(0.535, y - 0.012, name, color=color, fontsize=8.5,
                fontweight="bold", va="top")
        ax.text(0.535, y - 0.038, desc, color=TEXT_DIM, fontsize=7.2,
                va="top", linespacing=1.5)
        y -= 0.105

    # Stack diagram at bottom
    ax.text(0.52, 0.485, "Protocol Stack", color=TEXT_DIM, fontsize=7.5,
            fontweight="bold", va="top")
    stack = [
        (QDAP_GREEN,  "QFT Scheduler         → Emergency first, 374k decisions/s"),
        (BD_ORANGE,   "Adaptive FEC          → Up to 4× redundancy, fire-and-forget"),
        (CV2X_TEAL,   "Ghost Session         → 0-RTT reconnect, moving vehicles"),
        (UDP_BLUE,    "Delta Encoder         → 74.4% BSM compression"),
        (TEXT_DIM,    "TCP / UDP             → No hardware changes required"),
    ]
    sy = 0.455
    for color, label in stack:
        ax.add_patch(plt.Rectangle((0.52, sy), 0.46, 0.028,
                                   color=BG_CARD, ec="#30363D", lw=0.5))
        ax.add_patch(plt.Rectangle((0.52, sy), 0.005, 0.028, color=color))
        ax.text(0.532, sy + 0.014, label, color=color, fontsize=7.5,
                va="center", fontfamily="monospace")
        sy -= 0.030

    # Footnote
    ax.text(0.5, 0.02,
            "Pure Python 3.11+ · numpy · cryptography · msgpack · 444 tests · MIT License · pip install qdap",
            color=TEXT_DIM, fontsize=7, ha="center", va="bottom", style="italic")

    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 3 — DENM PDR vs Density (Urban + Highway)
# ─────────────────────────────────────────────────────────────────────────────

def page_pdr_density(pdf, rows):
    densities = [10, 25, 50, 75, 100]
    fig = dark_fig(11, 8.5)
    fig.suptitle("DENM PDR vs Vehicle Density  —  5-run Monte Carlo, seed = 42",
                 color=TEXT_PRIMARY, fontsize=11, fontweight="bold", y=0.97)

    for col, scenario in enumerate(["urban", "highway"]):
        ax = fig.add_subplot(1, 2, col + 1)
        style_ax(ax,
                 title=f"{'Urban Intersection (400m × 400m)' if scenario=='urban' else 'Highway Platoon (2 km)'}",
                 xlabel="Number of agents",
                 ylabel="DENM PDR (%)")
        for proto in PROTO_ORDER:
            pdrs = []
            for n in densities:
                a = agg(rows, scenario, n, proto)
                pdrs.append(a["denm_pdr"] if a else np.nan)
            lw = 2.5 if proto == "QDAP" else 1.3
            ls = "-" if proto in ("QDAP", "802.11bd") else "--"
            marker = "o" if proto == "QDAP" else "s"
            ax.plot(densities, pdrs, color=PROTO_COLORS[proto],
                    linewidth=lw, linestyle=ls, marker=marker,
                    markersize=5 if proto == "QDAP" else 3,
                    label=proto, zorder=3 if proto == "QDAP" else 2)

        ax.set_xticks(densities)
        ax.set_ylim(0, 100)
        ax.tick_params(colors=TEXT_DIM)
        # Crossover annotation for urban
        if scenario == "urban":
            ax.annotate("QDAP overtakes\n802.11bd at N≈75\n(CBR > 0.30)",
                        xy=(75, agg(rows, "urban", 75, "QDAP")["denm_pdr"]),
                        xytext=(55, 85),
                        color=QDAP_GREEN, fontsize=7,
                        arrowprops=dict(arrowstyle="->", color=QDAP_GREEN, lw=0.8),
                        multialignment="center")
        if col == 1:
            legend = ax.legend(loc="upper right", fontsize=7, framealpha=0.2,
                               facecolor=BG_CARD, edgecolor="#30363D",
                               labelcolor=TEXT_PRIMARY)

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 4 — DENM Deadline (< 50 ms) — the C-V2X smoking gun
# ─────────────────────────────────────────────────────────────────────────────

def page_deadline(pdf, rows):
    fig = dark_fig(11, 8.5)
    fig.suptitle("DENM Within 50 ms Safety Deadline  —  Urban Intersection",
                 color=TEXT_PRIMARY, fontsize=11, fontweight="bold", y=0.97)

    densities = [10, 25, 50, 75, 100]
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35,
                           left=0.08, right=0.97, top=0.91, bottom=0.08)

    # Top left: line chart deadline rate
    ax1 = fig.add_subplot(gs[0, 0])
    style_ax(ax1, title="Deadline Compliance vs Density",
             xlabel="Number of agents", ylabel="DENM < 50 ms (%)")
    for proto in PROTO_ORDER:
        ddls = [agg(rows, "urban", n, proto)["denm_ddl"]
                if agg(rows, "urban", n, proto) else np.nan
                for n in densities]
        lw = 2.5 if proto == "QDAP" else 1.2
        ax1.plot(densities, ddls, color=PROTO_COLORS[proto],
                 linewidth=lw, marker="o" if proto == "QDAP" else ".",
                 markersize=5 if proto == "QDAP" else 3, label=proto)
    ax1.axhline(50, color="#FF6B6B", linestyle=":", linewidth=0.8, alpha=0.7)
    ax1.text(12, 51.5, "50% threshold", color="#FF6B6B", fontsize=6.5)
    ax1.set_xticks(densities); ax1.set_ylim(0, 100)
    ax1.tick_params(colors=TEXT_DIM)

    # Top right: C-V2X SPS analysis — delivered vs within deadline
    ax2 = fig.add_subplot(gs[0, 1])
    style_ax(ax2, title="C-V2X Mode 4 — SPS Deadline Leak",
             xlabel="Number of agents", ylabel="%")
    cv2x_pdr  = [agg(rows, "urban", n, "C-V2X Mode 4")["denm_pdr"] for n in densities]
    cv2x_ddl  = [agg(rows, "urban", n, "C-V2X Mode 4")["denm_ddl"] for n in densities]
    x = np.arange(len(densities))
    w = 0.35
    ax2.bar(x - w/2, cv2x_pdr, w, color=CV2X_TEAL, alpha=0.9, label="Delivered")
    ax2.bar(x + w/2, cv2x_ddl, w, color=DSRC_RED, alpha=0.9, label="Within 50 ms")
    ax2.set_xticks(x); ax2.set_xticklabels(densities, color=TEXT_DIM, fontsize=8)
    ax2.set_ylim(0, 100); ax2.tick_params(colors=TEXT_DIM)
    ax2.legend(fontsize=7, framealpha=0.2, facecolor=BG_CARD,
               edgecolor="#30363D", labelcolor=TEXT_PRIMARY)
    ax2.text(0.5, 0.92,
             "~50% of delivered DENMs miss deadline\n(SPS offset = uniform[0, 100 ms])",
             transform=ax2.transAxes, ha="center", color=DSRC_RED,
             fontsize=7, multialignment="center",
             bbox=dict(boxstyle="round,pad=0.3", facecolor=BG_DARK,
                       edgecolor=DSRC_RED, linewidth=0.8))

    # Bottom left: bar chart N=75 urban
    ax3 = fig.add_subplot(gs[1, 0])
    style_ax(ax3, title="DENM < 50 ms at N=75  (Urban)",
             xlabel="Protocol", ylabel="Deadline compliance (%)")
    vals = [agg(rows, "urban", 75, p)["denm_ddl"] for p in PROTO_ORDER]
    colors = [PROTO_COLORS[p] for p in PROTO_ORDER]
    bars = ax3.bar(range(len(PROTO_ORDER)), vals, color=colors, alpha=0.9, width=0.6)
    ax3.set_xticks(range(len(PROTO_ORDER)))
    ax3.set_xticklabels([PROTO_LABELS[p] for p in PROTO_ORDER],
                        color=TEXT_DIM, fontsize=7.5)
    ax3.set_ylim(0, 100); ax3.tick_params(colors=TEXT_DIM)
    for bar, val in zip(bars, vals):
        ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
                 f"{val:.1f}%", ha="center", va="bottom",
                 color=TEXT_DIM, fontsize=7)

    # Bottom right: emergency p99 bar
    ax4 = fig.add_subplot(gs[1, 1])
    style_ax(ax4, title="Emergency p99 Latency at N=75  (Urban, log scale)",
             xlabel="Protocol", ylabel="p99 latency (ms)")
    vals_p99 = [agg(rows, "urban", 75, p)["p99_e"] for p in PROTO_ORDER]
    bars4 = ax4.bar(range(len(PROTO_ORDER)), vals_p99,
                    color=colors, alpha=0.9, width=0.6)
    ax4.set_yscale("log")
    ax4.set_xticks(range(len(PROTO_ORDER)))
    ax4.set_xticklabels([PROTO_LABELS[p] for p in PROTO_ORDER],
                        color=TEXT_DIM, fontsize=7.5)
    ax4.tick_params(colors=TEXT_DIM)
    ax4.axhline(50, color=BD_ORANGE, linestyle=":", linewidth=0.8)
    ax4.text(0.02, 52, "50 ms deadline", color=BD_ORANGE, fontsize=6.5)
    for bar, val in zip(bars4, vals_p99):
        ax4.text(bar.get_x() + bar.get_width()/2, bar.get_height() * 1.15,
                 f"{val:.1f}ms", ha="center", va="bottom",
                 color=TEXT_DIM, fontsize=6.5)

    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 5 — All scenarios bar comparison (N=75)
# ─────────────────────────────────────────────────────────────────────────────

def page_scenario_comparison(pdf, rows):
    fig = dark_fig(11, 8.5)
    fig.suptitle("Protocol Comparison Across All Scenarios  —  N = 75 agents",
                 color=TEXT_PRIMARY, fontsize=11, fontweight="bold", y=0.97)

    scenarios = ["urban", "highway", "cascade"]
    scenario_labels = ["Urban\nIntersection", "Highway\nPlatoon", "Emergency\nCascade"]
    metrics = [
        ("denm_pdr", "DENM PDR (%)", 0, 100),
        ("denm_ddl", "DENM < 50 ms (%)", 0, 100),
    ]

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.35,
                           left=0.07, right=0.97, top=0.91, bottom=0.12)

    for row_idx, (metric_key, metric_label, ymin, ymax) in enumerate(metrics):
        for col_idx, (sc, sc_label) in enumerate(zip(scenarios, scenario_labels)):
            ax = fig.add_subplot(gs[row_idx, col_idx])
            style_ax(ax, title=sc_label if row_idx == 0 else "",
                     ylabel=metric_label if col_idx == 0 else "")
            vals = [agg(rows, sc, 75, p)[metric_key]
                    if agg(rows, sc, 75, p) else 0
                    for p in PROTO_ORDER]
            bar_colors = [PROTO_COLORS[p] for p in PROTO_ORDER]
            bars = ax.bar(range(len(PROTO_ORDER)), vals, color=bar_colors,
                          alpha=0.9, width=0.6)
            ax.set_xticks(range(len(PROTO_ORDER)))
            if row_idx == 1:
                ax.set_xticklabels([PROTO_LABELS[p] for p in PROTO_ORDER],
                                   color=TEXT_DIM, fontsize=6.5)
            else:
                ax.set_xticklabels([""] * len(PROTO_ORDER))
            ax.set_ylim(ymin, ymax); ax.tick_params(colors=TEXT_DIM)
            for bar, val in zip(bars, vals):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.2,
                        f"{val:.0f}%", ha="center", va="bottom",
                        color=TEXT_DIM, fontsize=5.5)

    # Legend at bottom
    legend_patches = [mpatches.Patch(color=PROTO_COLORS[p], label=p)
                      for p in PROTO_ORDER]
    fig.legend(handles=legend_patches, loc="lower center", ncol=6,
               fontsize=8, framealpha=0.15, facecolor=BG_CARD,
               edgecolor="#30363D", labelcolor=TEXT_PRIMARY,
               bbox_to_anchor=(0.5, 0.02))

    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 6 — Radar + Highway detail
# ─────────────────────────────────────────────────────────────────────────────

def page_radar(pdf, rows):
    fig = dark_fig(11, 8.5)
    fig.suptitle("Multi-Metric Protocol Radar  —  Urban N=75",
                 color=TEXT_PRIMARY, fontsize=11, fontweight="bold", y=0.97)

    categories = ["DENM PDR", "DENM\n<50ms", "BSM PDR", "Low p99\nlatency", "Deadline\nreliability"]
    N_cat = len(categories)
    angles = np.linspace(0, 2 * np.pi, N_cat, endpoint=False).tolist()
    angles += angles[:1]

    ax = fig.add_subplot(121, polar=True)
    ax.set_facecolor(BG_CARD)
    ax.spines["polar"].set_color("#30363D")
    ax.tick_params(colors=TEXT_DIM, labelsize=7)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, color=TEXT_DIM, fontsize=8)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], color=TEXT_DIM, fontsize=6)
    ax.grid(color="#30363D", linewidth=0.5)

    for proto in PROTO_ORDER:
        a = agg(rows, "urban", 75, proto)
        if not a:
            continue
        # Low latency score: invert (lower is better), cap at 100
        lat_score = max(0, 100 - a["p99_e"])
        ddl_score = 100 if a["denm_ddl"] >= a["denm_pdr"] * 0.95 else a["denm_ddl"]
        vals = [a["denm_pdr"], a["denm_ddl"], a["bsm_pdr"], lat_score, ddl_score]
        vals += vals[:1]
        lw = 2.5 if proto == "QDAP" else 1.0
        alpha = 0.25 if proto == "QDAP" else 0.0
        ax.plot(angles, vals, color=PROTO_COLORS[proto], linewidth=lw, label=proto)
        ax.fill(angles, vals, color=PROTO_COLORS[proto], alpha=alpha)

    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15),
              fontsize=7, framealpha=0.15, facecolor=BG_CARD,
              edgecolor="#30363D", labelcolor=TEXT_PRIMARY)

    # Right: highway comparison N=75
    ax2 = fig.add_subplot(122)
    style_ax(ax2, title="Highway Platoon  —  DENM PDR vs Density",
             xlabel="Number of agents", ylabel="DENM PDR (%)")
    densities = [10, 25, 50, 75, 100]
    for proto in PROTO_ORDER:
        pdrs = [agg(rows, "highway", n, proto)["denm_pdr"]
                if agg(rows, "highway", n, proto) else np.nan
                for n in densities]
        lw = 2.5 if proto == "QDAP" else 1.2
        ls = "-" if proto in ("QDAP", "802.11bd") else "--"
        ax2.plot(densities, pdrs, color=PROTO_COLORS[proto],
                 linewidth=lw, linestyle=ls,
                 marker="o" if proto == "QDAP" else ".", markersize=5,
                 label=proto)
    ax2.set_xticks(densities); ax2.set_ylim(0, 60)
    ax2.tick_params(colors=TEXT_DIM)
    ax2.text(0.5, 0.92,
             "Lower absolute PDR: vehicles spread over 2km,\nmany pairs beyond 500m range",
             transform=ax2.transAxes, ha="center", color=TEXT_DIM,
             fontsize=7, multialignment="center",
             bbox=dict(boxstyle="round,pad=0.3", facecolor=BG_DARK,
                       edgecolor="#30363D", linewidth=0.5))

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 7 — Methodology + Limitations
# ─────────────────────────────────────────────────────────────────────────────

def page_methodology(pdf):
    fig = dark_fig(11, 8.5)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_DARK); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # Title
    ax.text(0.02, 0.97, "Simulation Methodology & Limitations",
            color=QDAP_GREEN, fontsize=13, fontweight="bold", va="top")
    ax.add_patch(plt.Rectangle((0.02, 0.955), 0.96, 0.002, color=QDAP_GREEN))

    # Two-column layout
    left_items = [
        ("Channel Model", QDAP_GREEN, [
            "• LOS: Two-ray ground reflection (h_t = h_r = 1.5 m)",
            "  PL = 40·log₁₀(d) − 7.04 dB  (validated: Gozalvez 2012)",
            "• NLOS: WINNER+ B1  PL = 36.7·log₁₀(d) + 22.7 + 26·log₁₀(f_c)",
            "• Shadowing: log-normal σ = 4 dB LOS, 8 dB NLOS",
            "• Fading: Nakagami-m  (m=2 LOS, m=1 Rayleigh NLOS)",
            "• SNR→PER: sigmoid fit to published BER curves per PHY",
        ]),
        ("Traffic & Scenarios", BD_ORANGE, [
            "• Urban: 400m × 400m, 4 corner buildings (170m × 170m AABB)",
            "  70% cars · 20% motorcycles · 10% pedestrians (VRU priority)",
            "• Highway: 2 km dual-lane, 100–130 km/h, 85% cars / 15% motos",
            "• Cascade: Highway + pedestrian hazard at x=500m",
            "• CBR ≈ 0.50 urban @ N=100 · ≈ 0.53 highway @ N=100",
        ]),
        ("Message Types", CV2X_TEAL, [
            "• BSM (SAE J2735):  400 B · 10 Hz · 100 ms deadline",
            "• DENM (ETSI EN 302 637):  250 B · event-driven · 50 ms deadline",
            "• Emergency injection: deterministic at step 25% and 65%",
            "  (same across all protocols — fair comparison guaranteed)",
        ]),
    ]
    right_items = [
        ("Monte Carlo Setup", UDP_BLUE, [
            "• 5 independent runs per (scenario, density, protocol)",
            "• Seeds: base_seed + run_idx × 1000  (reproducible)",
            "• Densities: N ∈ {10, 25, 50, 75, 100} agents",
            "• 450 total combinations  (5 densities × 6 protocols × 3 scenarios × 5 runs)",
            "• 20 s simulated time per run · 50 ms timestep",
        ]),
        ("Protocol Models", MQTT_PURPLE, [
            "• DSRC:  CBR-based collision  p_col = CBR² × 0.6  (empirical broadcast)",
            "• 802.11bd: +3 dB LDPC gain, better EDCA tuning",
            "• C-V2X:  SPS offset = uniform[0, 100 ms]  (3GPP TS 36.213)",
            "• MQTT:  P(tcp_fail) = 1−(1−PER)²  QoS 1 two-hop",
            "• QDAP:  P(fail) = PER^k  with k = 1.5–4.0 adaptive",
        ]),
        ("Limitations & Future Work", DSRC_RED, [
            "• V2X results are simulation-based — not real vehicle hardware",
            "• QDAP currently uses TCP; ETSI ITS-G5 PHY integration = future work",
            "• Multi-hop DENM relay not yet implemented",
            "• LOS: simplified distance threshold (350 m); full ray-cast = future work",
            "• Real-world validation on OBU/RSU hardware = next milestone",
        ]),
    ]

    y = 0.92
    for (title, color, lines), (title2, color2, lines2) in zip(left_items, right_items):
        ax.text(0.02, y, title, color=color, fontsize=8.5, fontweight="bold", va="top")
        ax.text(0.52, y, title2, color=color2, fontsize=8.5, fontweight="bold", va="top")
        y -= 0.022
        for line in lines:
            ax.text(0.02, y, line, color=TEXT_DIM, fontsize=7.2, va="top", fontfamily="monospace")
            y -= 0.018
        y2 = y + len(lines) * 0.018 + 0.022
        for line in lines2:
            ax.text(0.52, y2, line, color=TEXT_DIM, fontsize=7.2, va="top", fontfamily="monospace")
            y2 -= 0.018
        y -= 0.022

    # Footer / contact
    ax.add_patch(plt.Rectangle((0.02, 0.06), 0.96, 0.002, color="#30363D"))
    contact_items = [
        ("GitHub", "github.com/bahadir-bakla/qdap-protocol"),
        ("PyPI",   "pypi.org/project/qdap"),
        ("Website","qdap.dev"),
        ("Contact","bahadirbakla@gmail.com"),
    ]
    cx = 0.02
    for label, val in contact_items:
        ax.text(cx, 0.04, f"{label}:", color=TEXT_DIM, fontsize=7.5, va="top",
                fontweight="bold")
        ax.text(cx, 0.02, val, color=QDAP_GREEN, fontsize=7.5, va="top")
        cx += 0.24

    ax.text(0.5, 0.002, "Source data: simulations/v2x/results/v2x_results.csv · MIT License",
            color=TEXT_DIM, fontsize=6.5, ha="center", va="bottom", style="italic")

    pdf.savefig(fig, bbox_inches="tight", facecolor=BG_DARK)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate QDAP combined research PDF")
    parser.add_argument("--output", default=os.path.join(RESULTS_DIR, "QDAP_Research_Report.pdf"))
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    rows = load_csv()

    print(f"Generating: {args.output}")
    with PdfPages(args.output) as pdf:
        print("  Page 1/7 — Cover + headline metrics...")
        page_cover(pdf, rows)
        print("  Page 2/7 — Problem + Architecture...")
        page_architecture(pdf)
        print("  Page 3/7 — DENM PDR vs Density...")
        page_pdr_density(pdf, rows)
        print("  Page 4/7 — Deadline analysis (C-V2X smoking gun)...")
        page_deadline(pdf, rows)
        print("  Page 5/7 — All scenarios comparison...")
        page_scenario_comparison(pdf, rows)
        print("  Page 6/7 — Radar chart + Highway detail...")
        page_radar(pdf, rows)
        print("  Page 7/7 — Methodology + Limitations...")
        page_methodology(pdf)

        # PDF metadata
        d = pdf.infodict()
        d["Title"] = "QDAP: Emergency-Priority Protocol for V2X and High-Loss Networks"
        d["Author"] = "Bahadir Bakla"
        d["Subject"] = "V2X Protocol Research — QDAP vs DSRC, 802.11bd, C-V2X, MQTT"
        d["Keywords"] = "V2X DSRC C-V2X QDAP 802.11p emergency priority protocol"
        d["Creator"] = "QDAP Research Benchmark"

    size_kb = os.path.getsize(args.output) // 1024
    print(f"\nDone — {args.output}  ({size_kb} KB, 7 pages)")
    print(f"\nOpen with:  open \"{args.output}\"")


if __name__ == "__main__":
    main()
