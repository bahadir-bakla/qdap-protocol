"""
Benchmark Report Generator
============================

Generates matplotlib plots from benchmark results.
Outputs: throughput bars, latency percentiles, ACK overhead comparison,
         priority accuracy pie chart, and summary dashboard.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.style as mplstyle
    mplstyle.use('ggplot')
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def generate_plots(results: dict, output_dir: Path) -> None:
    """Generate all benchmark plots."""
    if not HAS_MATPLOTLIB:
        print("⚠️  matplotlib not installed — skipping plots")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    _plot_throughput(results, output_dir)
    _plot_latency(results, output_dir)
    _plot_ack_overhead(results, output_dir)
    _plot_priority(results, output_dir)


def _plot_throughput(results: dict, out: Path) -> None:
    sizes = [1, 10, 100]
    qdap_mb = []
    for s in sizes:
        key = f"throughput_{s}mb"
        if key in results:
            qdap_mb.append(results[key]["throughput_mbps"])
        else:
            return

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(sizes))

    ax.bar(x, qdap_mb, 0.6, label='QDAP TCP', color='#2ecc71', alpha=0.85)

    ax.set_xlabel('Transfer Boyutu')
    ax.set_ylabel('Throughput (MB/s)')
    ax.set_title('QDAP Throughput — Bulk Transfer')
    ax.set_xticks(x)
    ax.set_xticklabels([f'{s}MB' for s in sizes])
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out / "throughput.png", dpi=150)
    plt.close(fig)


def _plot_latency(results: dict, out: Path) -> None:
    if "latency_10k" not in results:
        return

    r = results["latency_10k"]
    percentiles = ['p50', 'p95', 'p99', 'p999']
    values = [r[f"{p}_ms"] for p in percentiles]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ['#27ae60', '#f39c12', '#e67e22', '#e74c3c']
    bars = ax.bar(percentiles, values, color=colors, alpha=0.85)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.01,
                f'{val:.3f}ms', ha='center', va='bottom', fontsize=10)

    ax.set_xlabel('Percentile')
    ax.set_ylabel('Latency (ms)')
    ax.set_title('QDAP Latency Distribution — 10K Messages')
    ax.grid(True, alpha=0.3, axis='y')

    fig.tight_layout()
    fig.savefig(out / "latency.png", dpi=150)
    plt.close(fig)


def _plot_ack_overhead(results: dict, out: Path) -> None:
    loss_rates = [0, 1, 5, 10]
    classical = []
    qdap = []

    for l in loss_rates:
        key = f"ack_overhead_{l}pct_loss"
        if key not in results:
            return
        classical.append(results[key]["classical_overhead_pct"])
        qdap.append(results[key]["qdap_overhead_pct"])

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(loss_rates))
    width = 0.35

    ax.bar(x - width / 2, classical, width, label='TCP ACK Overhead', color='#e74c3c', alpha=0.8)
    ax.bar(x + width / 2, qdap, width, label='QDAP Ghost Session', color='#2ecc71', alpha=0.8)

    ax.set_xlabel('Paket Kaybı Oranı')
    ax.set_ylabel('Overhead (%)')
    ax.set_title('ACK Overhead: TCP vs QDAP Ghost Session')
    ax.set_xticks(x)
    ax.set_xticklabels([f'%{l}' for l in loss_rates])
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    fig.tight_layout()
    fig.savefig(out / "ack_overhead.png", dpi=150)
    plt.close(fig)


def _plot_priority(results: dict, out: Path) -> None:
    if "priority_accuracy" not in results:
        return

    r = results["priority_accuracy"]
    accuracy = r["accuracy"] * 100

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.pie(
        [accuracy, 100 - accuracy],
        labels=['Doğru', 'Yanlış'],
        colors=['#2ecc71', '#e74c3c'],
        autopct='%1.1f%%',
        startangle=90,
    )
    ax.set_title(f'QFrame Priority Accuracy\n({r["n_trials"]} trial)')

    fig.tight_layout()
    fig.savefig(out / "priority_accuracy.png", dpi=150)
    plt.close(fig)
