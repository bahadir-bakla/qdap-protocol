"""
QDAP — Quantum-Inspired Dynamic Application Protocol
=====================================================

Klasik donanımda çalışan, quantum computing prensiplerinden
ilham alan uygulama katmanı iletişim protokolü.

Quickstart::

    import qdap

    # Emergency-priority send
    server = qdap.QDAPServer("0.0.0.0", 9000)
    client = qdap.QDAPClient("localhost", 9000)

    frame = qdap.QFrame.emergency(payload=b"SOS")
    await client.send(frame)

    # Adaptive FEC for lossy channels
    fec = qdap.AdaptiveFEC()
    coded, profile = fec.encode(b"data", is_emergency=True)

    # Delta compression for IoT streams
    enc = qdap.DeltaEncoder()
    compressed = enc.encode({"temp": 23.1, "co2": 412})

Core Components:
    QFrame / Subframe / SubframeType
        Superposition-inspired multi-payload frame multiplexer.

    QFTScheduler
        Fourier-based traffic analysis + deadline-aware emergency scheduling.
        Converges in t*=29 steps (Lemma 1b, lr=0.15).

    GhostSession / AdaptiveGhostSession
        Entanglement-inspired zero-ACK session protocol.
        AIC-optimal k=3 state machine.

    AdaptiveFEC
        Rate-adaptive Forward Error Correction (XOR systematic).
        Emergency: k=1,r=2 → 8.16× delivery improvement at 35% loss.

    DeltaEncoder
        74.4% bandwidth reduction for repetitive IoT data.

    BPTTMarkovEstimator
        Pure-Python Mini-LSTM channel predictor (no torch/numpy dependency).

    ParallelSender / ParallelReceiver
        Multi-stream parallel chunk transfer — 7.7× speedup.

    SessionTicket / SessionTicketStore
        0-RTT session resumption — 2.8× reconnect speedup.

    QDAPServer / QDAPClient
        Asyncio TCP server/client with built-in priority, FEC, ghost session.
"""

__version__ = "0.1.0"
__author__ = "QDAP Team"

# ── Core frame ────────────────────────────────────────────────────────────────
from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.frame.encoder import AmplitudeEncoder

# ── Scheduling ────────────────────────────────────────────────────────────────
from qdap.scheduler.qft_scheduler import QFTScheduler

# ── Sessions ──────────────────────────────────────────────────────────────────
from qdap.session.ghost_session import GhostSession
from qdap.broker.ghost_session_adaptive import AdaptiveGhostSession

# ── Transport ─────────────────────────────────────────────────────────────────
from qdap.transport.fec import (
    AdaptiveFEC,
    FECProfile,
    FECEncoder,
    FECDecoder,
    fec_effective_loss,
    fec_delivery_improvement,
    select_fec_profile,
)
from qdap.transport.parallel_sender import ParallelSender, ParallelReceiver

# ── Compression ───────────────────────────────────────────────────────────────
from qdap.compression.delta_encoder import DeltaEncoder

# ── Channel prediction ────────────────────────────────────────────────────────
from qdap.broker.markov_bptt import BPTTMarkovEstimator

# ── Security ─────────────────────────────────────────────────────────────────
from qdap.security.session_ticket import SessionTicket, SessionTicketStore

# ── Server ───────────────────────────────────────────────────────────────────
from qdap.server import QDAPServer, QDAPClient

__all__ = [
    # Frame
    "QFrame",
    "Subframe",
    "SubframeType",
    "AmplitudeEncoder",
    # Scheduling
    "QFTScheduler",
    # Sessions
    "GhostSession",
    "AdaptiveGhostSession",
    # Transport — FEC
    "AdaptiveFEC",
    "FECProfile",
    "FECEncoder",
    "FECDecoder",
    "fec_effective_loss",
    "fec_delivery_improvement",
    "select_fec_profile",
    # Transport — streaming
    "ParallelSender",
    "ParallelReceiver",
    # Compression
    "DeltaEncoder",
    # Channel prediction
    "BPTTMarkovEstimator",
    # Security
    "SessionTicket",
    "SessionTicketStore",
    # Server
    "QDAPServer",
    "QDAPClient",
    # Meta
    "__version__",
]
