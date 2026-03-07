"""
QDAP Quantum Fourier Transform Circuit
=========================================

Builds a Qiskit quantum circuit that applies QFT to a
classical time series, enabling mathematical comparison
with the classical FFT used by QFTScheduler.
"""

from __future__ import annotations

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import QFT
from qiskit_aer import AerSimulator
from qiskit.quantum_info import Statevector


class QDAPQuantumFourierTransform:
    """
    QDAP'ın FFT scheduler'ını Qiskit QFT devresiyle doğrula.

    Yaklaşım:
    1. Klasik zaman serisini quantum state'e encode et
    2. QFT devresi uygula
    3. Statevector'ı ölç
    4. Klasik FFT ile karşılaştır
    """

    def __init__(self, n_qubits: int = 6):
        """
        n_qubits: 2^n_qubits noktalı DFT
        6 qubit → 64 nokta (QFT Scheduler window_size=64 ile eşleşir!)
        """
        self.n_qubits = n_qubits
        self.n_points = 2 ** n_qubits
        self.simulator = AerSimulator(method='statevector')

    def build_circuit(self, time_series: np.ndarray) -> QuantumCircuit:
        """
        Zaman serisini QFT devresine encode et.

        Adımlar:
        1. Veriyi normalize et (quantum state normalizasyonu)
        2. Initialize gate ile amplitudes'ı set et
        3. QFT devresi uygula
        """
        assert len(time_series) == self.n_points, \
            f"Time series length must be {self.n_points}, got {len(time_series)}"

        # Normalizasyon — quantum state |ψ⟩ = Σ αᵢ|i⟩, Σ|αᵢ|² = 1
        norm = np.linalg.norm(time_series.astype(complex))
        if norm < 1e-10:
            normalized = np.ones(self.n_points, dtype=complex) / np.sqrt(self.n_points)
        else:
            normalized = time_series.astype(complex) / norm

        # Devre oluştur
        qc = QuantumCircuit(self.n_qubits)

        # Başlangıç durumunu set et
        qc.initialize(normalized.tolist(), range(self.n_qubits))

        # QFT devresi ekle
        qft_gate = QFT(self.n_qubits, approximation_degree=0, do_swaps=True)
        qc.append(qft_gate, range(self.n_qubits))

        return qc

    def run_qft(self, time_series: np.ndarray) -> np.ndarray:
        """
        QFT'yi statevector simulator'da çalıştır.
        Sonuç: frekans domain amplitudes (kompleks)
        """
        qc = self.build_circuit(time_series)
        qc.save_statevector()

        transpiled = transpile(qc, self.simulator)
        job = self.simulator.run(transpiled)
        result = job.result()

        statevector = np.array(result.get_statevector(transpiled))
        return statevector

    def run_classical_fft(self, time_series: np.ndarray) -> np.ndarray:
        """
        Klasik FFT — QDAP QFTScheduler'ın kullandığı yöntem.
        QFT ile karşılaştırma referansı.

        Qiskit QFT convention alignment:
        - Qiskit QFT: e^(+2πijk/N) with little-endian qubit ordering
        - NumPy FFT:  e^(-2πijk/N) with natural ordering
        - Fix: conjugate (sign flip) + bit-reversal permutation
        """
        norm = np.linalg.norm(time_series.astype(complex))
        if norm < 1e-10:
            normalized = np.ones(self.n_points, dtype=complex) / np.sqrt(self.n_points)
        else:
            normalized = time_series.astype(complex) / norm

        # FFT computation
        fft_result = np.fft.fft(normalized) / np.sqrt(self.n_points)

        # Conjugate to match QFT positive phase convention
        fft_result = np.conj(fft_result)

        # Apply bit-reversal permutation to match Qiskit's little-endian qubit ordering
        n_qubits = self.n_qubits
        reversed_indices = np.zeros(self.n_points, dtype=int)
        for i in range(self.n_points):
            rev = 0
            val = i
            for _ in range(n_qubits):
                rev = (rev << 1) | (val & 1)
                val >>= 1
            reversed_indices[i] = rev

        return fft_result[reversed_indices]
