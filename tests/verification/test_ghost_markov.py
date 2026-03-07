"""
Ghost Session Markov Chain Tests
===================================

Validates the Markov chain model used by Ghost Session
for loss detection and channel modeling.
"""

import pytest

from qdap.verification.ghost.markov_model import GhostSessionMarkovVerifier


@pytest.fixture(scope="module")
def verifier():
    return GhostSessionMarkovVerifier()


class TestGhostSessionMarkov:

    def test_ergodicity_low_loss(self, verifier):
        r = verifier.analyze_chain(p_loss=0.01, p_recovery=0.95, n_steps=50_000)
        assert r.is_ergodic, "Chain must be ergodic"

    def test_steady_state_accuracy(self, verifier):
        """Gözlemlenen kayıp oranı teorik değere yakın mı?"""
        r = verifier.analyze_chain(p_loss=0.05, p_recovery=0.90, n_steps=100_000)
        assert r.steady_state_error < 0.05, \
            f"Steady state error too high: {r.steady_state_error:.4f}"

    def test_detection_f1_above_threshold(self, verifier):
        r = verifier.analyze_chain(p_loss=0.05)
        assert r.f1_score > 0.50, f"F1 too low: {r.f1_score:.2%}"

    def test_high_loss_still_detects(self, verifier):
        """%20 kayıp oranında Ghost Session hâlâ çalışıyor mu?"""
        r = verifier.analyze_chain(p_loss=0.20, p_recovery=0.70)
        assert r.f1_score > 0.30, f"High loss F1 too low: {r.f1_score:.2%}"

    def test_mixing_time_finite(self, verifier):
        """Markov chain has finite mixing time."""
        r = verifier.analyze_chain(p_loss=0.05, p_recovery=0.90)
        assert r.mixing_time < 100, f"Mixing time too high: {r.mixing_time}"
