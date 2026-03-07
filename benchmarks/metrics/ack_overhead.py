"""
ACK Overhead Benchmark
========================

Compares Classical TCP ACK overhead vs QDAP Ghost Session.
Classical TCP: 40 bytes per ACK (IP+TCP headers)
QDAP: 0 ACK bytes + retransmit-only on loss detection.
"""

from __future__ import annotations

import hashlib
import os

from qdap.session.ghost_session import GhostSession
from qdap.frame.qframe import QFrame, Subframe, SubframeType


async def measure_ack_overhead(
    n_frames: int = 1000,
    loss_rate: float = 0.01,
) -> dict:
    """
    QDAP Ghost Session'ın ACK overhead'ini ölç.

    Klasik TCP: Her segment için 40 byte ACK
    QDAP: Sadece retransmit request (kayıp tespit edilince)
    """
    secret = os.urandom(32)
    sess_id = hashlib.sha256(b"bench").digest()

    alice = GhostSession(sess_id, secret)
    bob = GhostSession(sess_id, secret)

    ack_bytes_classical = 0
    ack_bytes_qdap = 0

    TCP_ACK_SIZE = 40  # IP header(20) + TCP header(20)

    for seq in range(n_frames):
        payload = b'B' * 1024
        frame = alice.send(payload, seq_num=seq)

        # Klasik TCP: Her frame için ACK
        ack_bytes_classical += TCP_ACK_SIZE

        # Deterministik paket kaybı simülasyonu
        lost = (hash((seq, 42)) % 100) < (loss_rate * 100)

        if not lost:
            bob.on_receive(frame)
            alice.implicit_ack(seq)
            # QDAP: ACK yok! Implicit.
        # else: kayıp → detect_loss() retransmit trigger eder

    total_data_bytes = n_frames * 1024

    # QDAP'ın retransmit overhead'i
    retransmits = alice.detect_loss()
    ack_bytes_qdap = len(retransmits) * 1024

    ghost_stats = alice.get_stats()

    return {
        "n_frames": n_frames,
        "loss_rate": loss_rate,
        "classical_ack_bytes": ack_bytes_classical,
        "qdap_ack_bytes": ack_bytes_qdap,
        "classical_overhead_pct": (ack_bytes_classical / total_data_bytes) * 100,
        "qdap_overhead_pct": (ack_bytes_qdap / total_data_bytes) * 100 if total_data_bytes > 0 else 0,
        "overhead_reduction": 1 - (ack_bytes_qdap / max(ack_bytes_classical, 1)),
        "ghost_total_sent": ghost_stats.total_sent,
        "ghost_total_acked": ghost_stats.total_acked,
    }
