"""
Born Rule Analog Tests
========================

Validates normalization, monotonicity, and statistical
properties of QDAP amplitude encoding.
"""

import pytest

from qdap.verification.amplitude.born_rule import BornRuleVerifier
from qdap.frame.qframe import Subframe, SubframeType


@pytest.fixture
def verifier():
    return BornRuleVerifier()


class TestBornRuleAnalog:

    def _make_subframes(self, deadlines):
        return [
            Subframe(payload=b'X' * 64, type=SubframeType.DATA,
                     deadline_ms=float(d))
            for d in deadlines
        ]

    def test_normalization_2_subframes(self, verifier):
        sfs = self._make_subframes([10, 50])
        r = verifier.verify(sfs)
        assert r.is_normalized, f"Norm error: {r.normalization_error}"

    def test_normalization_7_subframes(self, verifier):
        sfs = self._make_subframes([1, 5, 10, 20, 50, 100, 500])
        r = verifier.verify(sfs)
        assert r.is_normalized

    def test_monotonicity_deadline_ordering(self, verifier):
        """Düşük deadline → yüksek amplitude."""
        sfs = self._make_subframes([5, 50, 500])
        r = verifier.verify(sfs)
        assert r.monotonicity_holds, "High priority must have higher amplitude"

    def test_statistical_suite_99pct(self, verifier):
        stats = verifier.verify_statistical_suite(n_trials=1000)
        assert stats['pass_rate'] > 0.99, \
            f"Pass rate too low: {stats['pass_rate']:.2%}"

    def test_norm_error_machine_precision(self, verifier):
        stats = verifier.verify_statistical_suite(n_trials=500)
        assert stats['norm_error_max'] < 1e-9, \
            f"Max norm error exceeds tolerance: {stats['norm_error_max']}"
