"""
QFT ↔ FFT Equivalence Tests
===============================

Validates that Qiskit QFT and classical FFT produce
mathematically equivalent results for various traffic profiles.
"""

import numpy as np
import pytest

from qdap.verification.qft.equivalence import QFTEquivalenceVerifier


@pytest.fixture(scope="module")
def verifier():
    return QFTEquivalenceVerifier(n_qubits=4)  # 4 qubit = fast test (16 points)


class TestQFTEquivalence:

    def test_pure_sine(self, verifier):
        n = verifier.qft.n_points
        series = np.sin(2 * np.pi * np.arange(n) * 3 / n)
        result = verifier.verify_single(series, "pure_sine")
        assert result.is_equivalent, f"Max error: {result.max_abs_error}"

    def test_fidelity_high(self, verifier):
        n = verifier.qft.n_points
        series = np.random.RandomState(42).rand(n)
        result = verifier.verify_single(series, "random")
        assert result.fidelity > 0.999, f"Fidelity too low: {result.fidelity}"

    def test_energy_bands_match(self, verifier):
        n = verifier.qft.n_points
        series = np.sin(2 * np.pi * np.arange(n) * 1 / n)
        result = verifier.verify_single(series, "low_freq")
        assert result.energy_bands_match, "Energy bands must match for scheduler"

    def test_all_traffic_profiles(self, verifier):
        results = verifier.verify_suite()
        assert all(r.is_equivalent for r in results), \
            f"Failed profiles: {[r.test_name for r in results if not r.is_equivalent]}"

    def test_normalization_preserved(self, verifier):
        """QFT unitary transformation → norm korumalı."""
        n = verifier.qft.n_points
        series = np.random.RandomState(1).rand(n)
        qft_out = verifier.qft.run_qft(series)
        norm = np.sum(np.abs(qft_out) ** 2)
        assert abs(norm - 1.0) < 1e-6, f"QFT norm not preserved: {norm}"
