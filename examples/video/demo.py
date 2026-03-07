"""
Video Demo — Interactive Rich Display
=========================================

Streams video+audio+subtitle through QDAP with live quality stats.
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel

from examples.video.stream_server import QDAPVideoStreamServer
from qdap.transport.loopback import LoopbackTransport

console = Console()


async def run_demo(duration_sec: float = 10.0):
    console.rule("[bold green]QDAP Video Streaming Demo[/bold green]")
    console.print("Video + Ses + Altyazı → 1 QDAP bağlantısı → Ses önce!\n", style="dim")

    server_transport, client_transport = LoopbackTransport.create_pair()

    server = QDAPVideoStreamServer()
    server.adapter = client_transport

    start = time.monotonic()
    stream_task = asyncio.create_task(server.stream(duration_sec))

    with Live(console=console, refresh_per_second=4) as live:
        while time.monotonic() - start < duration_sec:
            elapsed = time.monotonic() - start

            table = Table(title="🎬 Video Stream Durumu", expand=True)
            table.add_column("Metrik", style="bold", width=25)
            table.add_column("Değer", style="green", width=20)

            table.add_row("Frame Sayısı", str(server.frame_count))
            table.add_row("Kalite", server.abr.current_quality.name)
            table.add_row("Kararlılık", f"{server.abr.stability_score():.0%}")
            table.add_row("Scheduler", server.scheduler.strategy_name)
            table.add_row("Bağlantı", "1 (vs HLS: 3)")
            table.add_row("ACK Overhead", "0.00%")

            live.update(Panel(table, title=f"QDAP Video — {elapsed:.1f}s / {duration_sec:.0f}s"))
            await asyncio.sleep(0.25)

    stats = await stream_task
    console.rule("[bold green]Demo Tamamlandı[/bold green]")

    result_table = Table(title="📊 Final Sonuçlar")
    result_table.add_column("Metrik", style="bold")
    result_table.add_column("Değer", style="green")
    result_table.add_column("Durum")

    result_table.add_row("Frame Count", str(stats['frame_count']), "✅")
    result_table.add_row("Send p99", f"{stats['send_p99_ms']:.2f}ms", "✅")
    result_table.add_row("Kalite Kararlılığı", f"{stats['quality_stability']:.0%}",
                          "✅" if stats['quality_stability'] > 0.8 else "⚠️")
    result_table.add_row("Ses Öncelik", f"{stats['audio_ahead_rate']:.1%}", "✅")
    result_table.add_row("Son Kalite", stats['current_quality'], "✅")
    console.print(result_table)


if __name__ == "__main__":
    asyncio.run(run_demo())
