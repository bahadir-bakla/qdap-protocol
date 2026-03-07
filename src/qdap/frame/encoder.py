"""
Amplitude Encoder — Quantum-Inspired Priority Weighting
========================================================

Klasik veriyi quantum-inspired amplitude vektörüne dönüştürür.
Her subframe'in önceliğini Born kuralı analogu ile hesaplar.

Temel: arXiv:2311.10375 — Quantum Data Encoding: A Comparative Analysis
Adaptasyon: Probability amplitudes → priority weights

Normalleşme Koşulu: Σ|αᵢ|² = 1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from qdap.frame.qframe import Subframe, SubframeType


@dataclass
class SessionHistory:
    """Tracks per-session urgency history for adaptive encoding."""

    _urgency_map: dict[int, float] = field(default_factory=dict)

    def get_urgency(self, session_id: int) -> float:
        """Return urgency weight for a session (default 1.0)."""
        return self._urgency_map.get(session_id, 1.0)

    def update_urgency(self, session_id: int, urgency: float) -> None:
        self._urgency_map[session_id] = urgency


class AmplitudeEncoder:
    """
    Klasik veriyi quantum-inspired amplitude vektörüne dönüştür.

    Her subframe'in önceliğini çoklu faktöre göre hesaplar ve
    L2 normalleştirme ile quantum state normalleşmesini taklit eder.

    Usage:
        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)
        send_order = encoder.decode_schedule(amplitudes)
    """

    def __init__(self):
        self.session_history = SessionHistory()

    def encode(self, subframes: list[Subframe]) -> np.ndarray:
        """
        Her subframe'in önceliğini amplitude vektörüne dönüştür.

        Normalleşme koşulu: Σ|αᵢ|² = 1 (quantum state normalization).

        Args:
            subframes: List of subframes to encode.

        Returns:
            Normalized amplitude vector (float64).
        """
        if not subframes:
            return np.array([], dtype=np.float64)

        raw_weights = np.array(
            [self._compute_priority(sf) for sf in subframes],
            dtype=np.float64,
        )

        # L2 normalleştirme — quantum state normalleşmesini taklit eder
        norm = np.linalg.norm(raw_weights)
        if norm < 1e-12:
            # Edge case: all zero weights → uniform
            n = len(subframes)
            return np.full(n, 1.0 / np.sqrt(n), dtype=np.float64)

        amplitudes = raw_weights / norm
        return amplitudes

    def _compute_priority(self, sf: Subframe) -> float:
        """
        Compute raw priority weight for a subframe.

        Priority = f(deadline, size, type, history)

        Shorter deadline → higher priority (inverse relationship).
        Smaller size → higher priority (log-scaled).
        Type-specific base priority from SubframeType.priority_map.
        Session history urgency factor.
        """
        from qdap.frame.qframe import SubframeType

        # Deadline: shorter deadline → higher urgency
        deadline_weight = 1.0 / (sf.deadline_ms + 1e-9)

        # Size: smaller payloads are typically more urgent (sensor pings, RPCs)
        size_weight = 1.0 / np.log2(sf.size_bytes + 2)

        # Type: inherent priority per subframe type
        type_weight = SubframeType.priority_map().get(sf.type, 1.0)

        # Session history: learned urgency
        history_weight = self.session_history.get_urgency(sf.session_id)

        return deadline_weight * size_weight * type_weight * history_weight

    def decode_schedule(self, amplitudes: np.ndarray) -> list[int]:
        """
        Amplitude vektöründen transmission sırasını çıkar.

        En yüksek |α|² → en önce gönder (Born kuralı analogu).

        Args:
            amplitudes: Normalized amplitude vector.

        Returns:
            Indices sorted by descending probability (highest first).
        """
        if len(amplitudes) == 0:
            return []

        probabilities = amplitudes**2  # Born kuralı analogu
        return np.argsort(probabilities)[::-1].tolist()

    def encode_frame(
        self,
        subframes: list[Subframe],
        session_id: int = 0,
    ) -> QFrame:
        """
        Convenience method: encode subframes and return a complete QFrame.

        Computes amplitude vector from subframe priorities and creates
        a ready-to-send QFrame.

        Args:
            subframes: List of subframes to encode.
            session_id: Session identifier.

        Returns:
            QFrame with computed amplitude vector.
        """
        from qdap.frame.qframe import QFrame, FrameType

        amplitudes = self.encode(subframes)
        return QFrame.create(
            subframes=subframes,
            session_id=session_id,
            frame_type=FrameType.DATA,
            amplitude_vector=amplitudes.astype(np.float32),
        )
