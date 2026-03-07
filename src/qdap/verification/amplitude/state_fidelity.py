"""
State Fidelity Measurement
=============================

Measures the fidelity between QDAP amplitude vectors and
quantum states using Qiskit's Statevector class.

Fidelity F = |⟨ψ_ideal|ψ_qdap⟩|² ∈ [0, 1]
F = 1.0 → Perfect quantum state
F > 0.99 → "quantum-compatible encoding"
"""

from __future__ import annotations

import numpy as np
from qiskit.quantum_info import Statevector, state_fidelity

from qdap.frame.encoder import AmplitudeEncoder
from qdap.frame.qframe import Subframe


class StateFidelityMeasurer:
    """
    QDAP amplitude vektörünü gerçek bir quantum state olarak yorumla
    ve Qiskit Statevector ile fidelity'sini ölç.
    """

    def __init__(self):
        self.encoder = AmplitudeEncoder()

    def measure(self, subframes: list[Subframe]) -> dict:
        amplitudes = self.encoder.encode(subframes)
        n = len(amplitudes)

        # Quantum state boyutuna pad et (2^k)
        n_qubits = int(np.ceil(np.log2(n))) if n > 1 else 1
        state_dim = 2 ** n_qubits

        padded = np.zeros(state_dim, dtype=complex)
        padded[:n] = amplitudes.astype(complex)
        # Yeniden normalleştir (padding sonrası)
        norm = np.linalg.norm(padded)
        if norm > 1e-10:
            padded /= norm

        qdap_state = Statevector(padded)

        # "İdeal" state: uniform superposition (maksimum entropi referans)
        ideal_state = Statevector(
            np.ones(state_dim, dtype=complex) / np.sqrt(state_dim)
        )

        # Fidelity
        fid = state_fidelity(qdap_state, ideal_state)

        return {
            "n_subframes": n,
            "n_qubits": n_qubits,
            "state_dim": state_dim,
            "fidelity": float(fid),
            "is_valid_state": bool(abs(np.sum(np.abs(padded) ** 2) - 1.0) < 1e-9),
            "amplitudes": amplitudes.tolist(),
            "probabilities": (amplitudes ** 2).tolist(),
        }
