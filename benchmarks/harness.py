"""
QDAP Benchmark Harness
========================

Core infrastructure for running, measuring, and comparing
QDAP performance against TCP baselines.
"""

from __future__ import annotations

import asyncio
import time
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from rich.console import Console
from rich.table import Table
from rich.progress import Progress

console = Console()


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""

    name: str
    protocol: str     # "TCP_BASELINE" | "QDAP_TCP" | "QDAP_QUIC"
    duration_sec: float
    throughput_mbps: float
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_p999_ms: float = 0.0
    ack_bytes: int = 0
    total_bytes: int = 0
    priority_accuracy: float = 0.0    # 0.0 - 1.0
    loss_detected: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ack_overhead_pct(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.ack_bytes / self.total_bytes) * 100


@dataclass
class BenchmarkSuite:
    """
    Tüm senaryoları çalıştıran ana harness.
    """

    host: str = "127.0.0.1"
    port: int = 19000
    warmup_s: int = 1
    runs: int = 3

    async def run_scenario(
        self,
        name: str,
        fn: Callable[[], Awaitable[BenchmarkResult]],
    ) -> BenchmarkResult:
        """Bir senaryoyu warmup + N run ile çalıştır."""
        console.print(f"\n[bold cyan]▶ {name}[/bold cyan]")

        # Warmup
        console.print(f"  Warmup ({self.warmup_s}s)...", end="")
        await asyncio.sleep(self.warmup_s)
        console.print(" done")

        # Ölçüm
        results = []
        with Progress() as progress:
            task = progress.add_task(f"  Running {self.runs} iterations", total=self.runs)
            for i in range(self.runs):
                r = await fn()
                results.append(r)
                progress.advance(task)

        # Medyan al
        best = sorted(results, key=lambda r: r.throughput_mbps)[len(results) // 2]
        return best

    def print_comparison(self, baseline: BenchmarkResult, qdap: BenchmarkResult) -> None:
        """Baseline vs QDAP karşılaştırma tablosu."""
        table = Table(title=f"📊 {baseline.name} — Karşılaştırma")
        table.add_column("Metrik", style="bold")
        table.add_column("TCP Baseline", style="red")
        table.add_column("QDAP", style="green")
        table.add_column("Δ", style="yellow")

        def delta(a: float, b: float, fmt: str = ".2f", lower_is_better: bool = False) -> str:
            diff = b - a
            pct = (diff / a * 100) if a != 0 else 0
            sign = "+" if diff > 0 else ""
            arrow = "↑" if (diff > 0) != lower_is_better else "↓"
            return f"{sign}{diff:{fmt}} ({arrow}{abs(pct):.1f}%)"

        table.add_row(
            "Throughput (MB/s)",
            f"{baseline.throughput_mbps:.2f}",
            f"{qdap.throughput_mbps:.2f}",
            delta(baseline.throughput_mbps, qdap.throughput_mbps),
        )
        table.add_row(
            "Latency p99 (ms)",
            f"{baseline.latency_p99_ms:.3f}",
            f"{qdap.latency_p99_ms:.3f}",
            delta(baseline.latency_p99_ms, qdap.latency_p99_ms, lower_is_better=True),
        )
        table.add_row(
            "ACK Overhead (%)",
            f"{baseline.ack_overhead_pct:.2f}%",
            f"{qdap.ack_overhead_pct:.2f}%",
            delta(baseline.ack_overhead_pct, qdap.ack_overhead_pct, lower_is_better=True),
        )
        table.add_row(
            "Priority Accuracy",
            "N/A (FIFO)",
            f"{qdap.priority_accuracy:.1%}",
            "—",
        )

        console.print(table)
