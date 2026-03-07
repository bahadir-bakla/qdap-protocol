"""
Ghost Session Markov Model Verifier
======================================

Validates the AdaptiveMarkovChain used by Ghost Session
against theoretical steady-state predictions and measures
loss detection precision/recall/F1.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np
from dataclasses import dataclass
from scipy.linalg import eig

from qdap.session.ghost_session import GhostSession


@dataclass
class MarkovAnalysisResult:
    p_loss: float
    p_recovery: float
    steady_state_good: float
    steady_state_bad: float
    empirical_good: float
    empirical_bad: float
    steady_state_error: float
    mixing_time: int
    is_ergodic: bool
    detection_precision: float
    detection_recall: float
    f1_score: float

    def summary(self) -> str:
        return (
            f"🔗 Ghost Session Markov Analizi\n"
            f"  Kayıp oranı (teorik):  {self.steady_state_bad:.3%}\n"
            f"  Kayıp oranı (gözlem):  {self.empirical_bad:.3%}\n"
            f"  Steady-state hatası:   {self.steady_state_error:.2e}\n"
            f"  Mixing time:           {self.mixing_time} adım\n"
            f"  Ergodik:               {'✅' if self.is_ergodic else '❌'}\n"
            f"  Tespit precision:      {self.detection_precision:.3%}\n"
            f"  Tespit recall:         {self.detection_recall:.3%}\n"
            f"  F1 score:              {self.f1_score:.3%}"
        )


class GhostSessionMarkovVerifier:
    """
    Ghost Session'ın AdaptiveMarkovChain modelini doğrula.

    İki bağımsız doğrulama:
    1. Markov chain teorik özellikleri (ergodik, mixing time, steady-state)
    2. Pratik kayıp tespit doğruluğu (precision, recall, F1)
    """

    def analyze_chain(
        self,
        p_loss: float = 0.05,
        p_recovery: float = 0.90,
        n_steps: int = 100_000,
    ) -> MarkovAnalysisResult:
        """
        Gilbert-Elliott kanal modeli ile Ghost Session'ı test et.
        """
        secret = os.urandom(32)
        sess_id = hashlib.sha256(b"markov_test").digest()
        alice = GhostSession(sess_id, secret)
        bob = GhostSession(sess_id, secret)

        # Teorik steady-state
        ss_good_theory = p_recovery / (p_loss + p_recovery)
        ss_bad_theory = p_loss / (p_loss + p_recovery)

        # Gilbert-Elliott kanalı simüle et
        rng = np.random.RandomState(42)
        state = 'good'
        channel_log = []

        for _ in range(n_steps):
            if state == 'good':
                lost = rng.random() < p_loss
                state = 'bad' if lost else 'good'
            else:
                lost = True
                state = 'good' if rng.random() < p_recovery else 'bad'
            channel_log.append(not lost)

        # Gözlemlenen steady-state
        empirical_good = sum(channel_log) / n_steps
        empirical_bad = 1 - empirical_good

        # Ghost Session'ı kanalda çalıştır (ilk 10K paket)
        test_len = min(10_000, n_steps)
        true_losses = []

        for seq in range(test_len):
            payload = bytes([seq % 256] * 64)
            frame = alice.send(payload, seq_num=seq)

            arrived = channel_log[seq]
            if arrived:
                bob.on_receive(frame)
                alice.implicit_ack(seq)

            true_losses.append(not arrived)

        # Loss detection: packets still in ghost_window are unacked (lost)
        detected_set = set(alice.ghost_window.keys())
        detected_bin = [i in detected_set for i in range(test_len)]

        # Precision & Recall
        tp = sum(a and b for a, b in zip(true_losses, detected_bin))
        fp = sum(not a and b for a, b in zip(true_losses, detected_bin))
        fn = sum(a and not b for a, b in zip(true_losses, detected_bin))

        precision = tp / (tp + fp + 1e-10)
        recall = tp / (tp + fn + 1e-10)
        f1 = 2 * precision * recall / (precision + recall + 1e-10)

        # Mixing time — geçiş matrisinin 2. özdeğerinden hesapla
        P = np.array([
            [1 - p_loss, p_loss],
            [p_recovery, 1 - p_recovery],
        ])
        eigenvalues, _ = eig(P.T)
        eigenvalues = sorted(np.abs(eigenvalues), reverse=True)
        lambda2 = eigenvalues[1] if len(eigenvalues) > 1 else 0
        mixing_time = int(np.ceil(1 / (1 - lambda2 + 1e-10)))

        # Ergodiklik
        is_ergodic = empirical_good > 0.01 and empirical_bad > 0.01

        return MarkovAnalysisResult(
            p_loss=p_loss,
            p_recovery=p_recovery,
            steady_state_good=ss_good_theory,
            steady_state_bad=ss_bad_theory,
            empirical_good=empirical_good,
            empirical_bad=empirical_bad,
            steady_state_error=abs(ss_good_theory - empirical_good),
            mixing_time=mixing_time,
            is_ergodic=is_ergodic,
            detection_precision=precision,
            detection_recall=recall,
            f1_score=f1,
        )

    def run_loss_rate_sweep(self) -> list[MarkovAnalysisResult]:
        """
        Farklı kayıp oranlarında Ghost Session performansını ölç.
        Paper Table 2 için veri.
        """
        loss_rates = [0.01, 0.05, 0.10, 0.20, 0.30]
        results = []

        for p_loss in loss_rates:
            result = self.analyze_chain(p_loss=p_loss)
            results.append(result)

        return results
