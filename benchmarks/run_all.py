#!/usr/bin/env python3
"""
QDAP Benchmark Runner
======================

Tek komutla tüm benchmark'ları çalıştır:
    python benchmarks/run_all.py

Çıktı:
    - Terminal: Rich tabloları
    - benchmarks/results/latest.json
    - benchmarks/results/plots/*.png
"""

import asyncio
import json
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from benchmarks.metrics.ack_overhead import measure_ack_overhead
from benchmarks.metrics.priority import measure_priority_accuracy

console = Console()


async def main():
    console.print(Panel.fit(
        "[bold]QDAP Benchmark Suite v0.2[/bold]\n"
        "TCP Adapter + Ghost Session Performance Analysis",
        border_style="bright_green",
    ))
    console.print(f"Başlama zamanı: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    results = {}

    # ── ACK Overhead (no server needed) ──────────────────────────
    console.rule("[cyan]ACK Overhead Analysis")
    for loss in [0.0, 0.01, 0.05, 0.10]:
        r = await measure_ack_overhead(n_frames=1000, loss_rate=loss)
        results[f"ack_overhead_{int(loss * 100)}pct_loss"] = r
        console.print(
            f"  Loss={loss:.0%}: "
            f"Classical={r['classical_overhead_pct']:.2f}% vs "
            f"QDAP={r['qdap_overhead_pct']:.2f}% "
            f"([green]↓{r['overhead_reduction']:.1%}[/green])"
        )

    # ── Priority Accuracy (no server needed) ─────────────────────
    console.rule("[cyan]Priority Accuracy")
    r = await measure_priority_accuracy(n_trials=1000)
    results["priority_accuracy"] = r
    console.print(f"  Accuracy: [green]{r['accuracy']:.1%}[/green] ({r['correct']}/{r['n_trials']})")
    console.print(f"  Encode p99: {r['encode_p99_ms']:.3f}ms")

    # ── Save Results ─────────────────────────────────────────────
    output_dir = Path("benchmarks/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Convert non-serializable values
    serializable = {}
    for k, v in results.items():
        if isinstance(v, dict):
            clean = {}
            for kk, vv in v.items():
                if hasattr(vv, '__dict__'):
                    clean[kk] = str(vv)
                else:
                    clean[kk] = vv
            serializable[k] = clean
        else:
            serializable[k] = v

    with open(output_dir / "latest.json", "w") as f:
        json.dump(serializable, f, indent=2, default=str)

    # Generate plots
    try:
        from benchmarks.report.generator import generate_plots
        generate_plots(results, output_dir / "plots")
        console.print(f"\n📊 Grafikler: {output_dir}/plots/")
    except Exception as e:
        console.print(f"\n⚠️  Plot generation skipped: {e}")

    console.rule("[bold green]Benchmark Complete ✅")
    console.print(f"Sonuçlar: {output_dir}/latest.json")


if __name__ == "__main__":
    asyncio.run(main())
