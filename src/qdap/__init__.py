"""
QDAP — Quantum-Inspired Dynamic Application Protocol
=====================================================

Klasik donanımda çalışan, quantum computing prensiplerinden
ilham alan uygulama katmanı iletişim protokolü.

Core Components:
    - QFrame Multiplexer  → Superposition-inspired multi-payload encoding
    - QFT Packet Scheduler → Fourier-based traffic analysis & scheduling
    - Ghost Session       → Entanglement-inspired stateless acknowledgment
"""

__version__ = "0.1.0"
__author__ = "QDAP Team"

from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.frame.encoder import AmplitudeEncoder
from qdap.scheduler.qft_scheduler import QFTScheduler
from qdap.session.ghost_session import GhostSession
from qdap.server import QDAPServer, QDAPClient

__all__ = [
    "QFrame",
    "Subframe",
    "SubframeType",
    "AmplitudeEncoder",
    "QFTScheduler",
    "GhostSession",
    "QDAPServer",
    "QDAPClient",
    "__version__",
]
