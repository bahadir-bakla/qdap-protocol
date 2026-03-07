"""
QFT ↔ FFT Equivalence Verifier
=================================

Proves that QDAP's classical FFT-based scheduler produces
mathematically equivalent results to a true Quantum Fourier Transform.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import List

from qdap.verification.qft.circuit import QDAPQuantumFourierTransform


@dataclass
class EquivalenceResult:
    """QFT ↔ FFT denklik test sonucu."""
    test_name: str
    n_qubits: int
    n_points: int
    max_abs_error: float
    mean_abs_error: float
    fidelity: float          # |⟨QFT|FFT⟩|² ∈ [0,1]
    is_equivalent: bool
    tolerance: float
    energy_bands_match: bool

    def summary(self) -> str:
        status = "✅ EŞDEĞERLİK KANITLANDI" if self.is_equivalent else "❌ FARK VAR"
        return (
            f"{status} [{self.test_name}]\n"
            f"  Max hata:       {self.max_abs_error:.2e}\n"
            f"  Ortalama hata:  {self.mean_abs_error:.2e}\n"
            f"  Fidelity:       {self.fidelity:.6f}\n"
            f"  Enerji bantları: {'Eşleşiyor ✅' if self.energy_bands_match else 'Uyuşmuyor ❌'}"
        )


class QFTEquivalenceVerifier:
    """
    QDAP'ın ana iddiasını kanıtla:
    'QFTScheduler'ın kullandığı klasik FFT, gerçek QFT ile
     matematiksel olarak eşdeğerdir.'
    """

    TOLERANCE = 1e-5

    def __init__(self, n_qubits: int = 6):
        self.qft = QDAPQuantumFourierTransform(n_qubits)

    def verify_single(
        self,
        time_series: np.ndarray,
        test_name: str = "custom",
    ) -> EquivalenceResult:
        """Tek bir zaman serisi için QFT ↔ FFT denkliğini test et."""
        qft_result = self.qft.run_qft(time_series)
        fft_result = self.qft.run_classical_fft(time_series)

        # Eleman-bazlı hata
        diff = np.abs(qft_result - fft_result)
        max_abs_error = float(diff.max())
        mean_abs_error = float(diff.mean())

        # Quantum fidelity: |⟨ψ_QFT|ψ_FFT⟩|²
        fidelity = float(np.abs(np.dot(np.conj(qft_result), fft_result)) ** 2)

        # Enerji bantları karşılaştırması
        qft_energy = self._compute_energy_bands(np.abs(qft_result) ** 2)
        fft_energy = self._compute_energy_bands(np.abs(fft_result) ** 2)
        bands_match = all(
            abs(qft_energy[k] - fft_energy[k]) < 0.01
            for k in ['low', 'mid', 'high']
        )

        return EquivalenceResult(
            test_name=test_name,
            n_qubits=self.qft.n_qubits,
            n_points=self.qft.n_points,
            max_abs_error=max_abs_error,
            mean_abs_error=mean_abs_error,
            fidelity=fidelity,
            is_equivalent=max_abs_error < self.TOLERANCE,
            tolerance=self.TOLERANCE,
            energy_bands_match=bands_match,
        )

    def verify_suite(self) -> List[EquivalenceResult]:
        """
        Paper için kapsamlı doğrulama seti.
        Farklı trafik tiplerini temsil eden zaman serileri.
        """
        n = self.qft.n_points
        test_cases = {
            "pure_low_freq": np.sin(2 * np.pi * np.arange(n) * 2 / n),
            "pure_high_freq": np.sin(2 * np.pi * np.arange(n) * (n // 4 - 1) / n),
            "mixed_traffic": (
                0.7 * np.sin(2 * np.pi * np.arange(n) * 3 / n)
                + 0.2 * np.sin(2 * np.pi * np.arange(n) * (n // 8) / n)
                + 0.1 * np.random.RandomState(42).randn(n)
            ),
            "burst_traffic": np.where(
                np.random.RandomState(7).rand(n) > 0.9,
                np.random.RandomState(7).randn(n) * 5, 0.01
            ),
            "constant": np.ones(n) * 0.5,
            "random_uniform": np.random.RandomState(99).rand(n),
        }

        results = []
        for name, series in test_cases.items():
            r = self.verify_single(series, test_name=name)
            results.append(r)

        return results

    def _compute_energy_bands(self, power: np.ndarray) -> dict:
        """Güç spektrumunu 3 banda böl (QFTScheduler ile aynı mantık)."""
        n = len(power)
        total = power.sum() + 1e-10
        return {
            'low': float(power[:n // 10].sum() / total),
            'mid': float(power[n // 10:4 * n // 10].sum() / total),
            'high': float(power[4 * n // 10:].sum() / total),
        }
