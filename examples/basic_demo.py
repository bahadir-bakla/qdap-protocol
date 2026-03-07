#!/usr/bin/env python3
"""
QDAP Basic Demo
================

Demonstrates the three core QDAP components working together:
  1. QFrame + AmplitudeEncoder (superposition-inspired multiplexing)
  2. QFT Packet Scheduler (Fourier-based traffic analysis)
  3. Ghost Session (entanglement-inspired implicit ACK)

Run: python -m qdap.examples.basic_demo
"""

import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.frame.encoder import AmplitudeEncoder
from qdap.scheduler.qft_scheduler import QFTScheduler, Packet
from qdap.session.ghost_session import GhostSession

console = Console()


def demo_qframe():
    """Demo 1: QFrame Multiplexer with AmplitudeEncoder."""
    console.rule("[bold cyan]Demo 1: QFrame + AmplitudeEncoder")
    console.print()

    # Three streams: video (big, relaxed), audio (medium, urgent), cursor (tiny, critical)
    video_chunk = b"\x00" * 100_000  # 100KB video
    audio_chunk = b"\x00" * 3_000    # 3KB audio
    cursor_pos = b"\x00" * 200       # 200B cursor

    subframes = [
        Subframe(payload=video_chunk, type=SubframeType.DATA, deadline_ms=16, seq_num=1),
        Subframe(payload=audio_chunk, type=SubframeType.DATA, deadline_ms=8, seq_num=2),
        Subframe(payload=cursor_pos, type=SubframeType.DATA, deadline_ms=4, seq_num=3),
    ]

    # Auto-encode with priority-based amplitudes
    frame = QFrame.create_with_encoder(subframes=subframes, session_id=0x1234)

    # Display results
    table = Table(title="QFrame Subframes")
    table.add_column("Index", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Deadline", justify="right")
    table.add_column("Amplitude (α)", justify="right", style="yellow")
    table.add_column("|α|²", justify="right", style="red")

    for i, (sf, amp) in enumerate(zip(frame.subframes, frame.amplitude_vector)):
        table.add_row(
            str(i),
            sf.type.name,
            f"{sf.size_bytes:,}B",
            f"{sf.deadline_ms:.0f}ms",
            f"{amp:.4f}",
            f"{amp**2:.4f}",
        )

    console.print(table)
    console.print(f"\n  Σ|α|² = {np.sum(frame.amplitude_vector.astype(np.float64)**2):.6f}  ✅")
    console.print(f"  Send order: {frame.send_order}")
    console.print(f"  → Cursor (idx {frame.send_order[0]}) gönderilir ilk! 🎯")

    # Serialize/deserialize roundtrip
    data = frame.serialize()
    recovered = QFrame.deserialize(data)
    console.print(f"\n  Wire size: {len(data):,} bytes")
    console.print(f"  Roundtrip: {'✅ OK' if recovered.subframe_count == 3 else '❌ FAIL'}")
    console.print()


def demo_qft_scheduler():
    """Demo 2: QFT Packet Scheduler."""
    console.rule("[bold cyan]Demo 2: QFT Packet Scheduler")
    console.print()

    scheduler = QFTScheduler(window_size=64)

    # Feed latency-sensitive traffic (small, frequent packets)
    for i in range(64):
        scheduler.observe(Packet(
            payload=b"\x00" * (50 + (i % 20) * 5),
            deadline_ms=5 + (i % 10),
        ))

    # Show spectrum report
    report = scheduler.get_spectrum_report()
    console.print(Panel(report, title="QFT Spectral Analysis", border_style="cyan"))

    console.print(f"\n  Current strategy: [bold]{scheduler.strategy_name}[/bold]")
    console.print()


def demo_ghost_session():
    """Demo 3: Ghost Session Protocol."""
    console.rule("[bold cyan]Demo 3: Ghost Session Protocol")
    console.print()

    session_id = b"demo-session-001"
    shared_secret = b"quantum-shared-secret-2024"

    alice = GhostSession(session_id, shared_secret)
    bob = GhostSession(session_id, shared_secret)

    console.print("  Alice ve Bob aynı Ghost State'i paylaşıyor...")
    console.print()

    # Alice sends 10 messages
    for i in range(10):
        frame = alice.send(payload=f"Message #{i}".encode(), seq_num=i)

        # Bob receives (no ACK sent!)
        verified = bob.on_receive(frame)

        # Simulate Alice learning about delivery (side-channel / subsequent frame)
        if verified:
            alice.implicit_ack(i)

    stats = alice.get_stats()

    table = Table(title="Ghost Session Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    table.add_row("Total Sent", str(stats.total_sent))
    table.add_row("Total ACK'd", str(stats.total_acked))
    table.add_row("Pending", str(stats.current_pending))
    table.add_row("Channel State", stats.channel_state)
    table.add_row("ACK packets sent", "[bold red]0[/bold red]  ← entanglement!")

    console.print(table)
    console.print("\n  ✅ 10 mesaj gönderildi, [bold]sıfır ACK paketi[/bold] gönderildi!")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold]QDAP — Quantum-Inspired Dynamic Application Protocol[/bold]\n"
        "Klasik donanımda çalışan, quantum prensiplerinden ilham alan protokol",
        border_style="bright_blue",
    ))
    console.print()

    demo_qframe()
    demo_qft_scheduler()
    demo_ghost_session()

    console.rule("[bold green]All demos completed ✅")


if __name__ == "__main__":
    main()
