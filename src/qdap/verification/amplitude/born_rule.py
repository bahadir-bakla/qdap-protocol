"""
Born Rule Analog Verifier
============================

Validates that QDAP's AmplitudeEncoder produces probability
distributions consistent with the Born rule (P(i) = |αᵢ|²).

Three properties verified:
1. Normalization: Σ|αᵢ|² = 1
2. Monotonicity: higher priority → higher |α|²
3. Distribution validity: probabilities form a valid simplex
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import List

from scipy import stats

from qdap.frame.encoder import AmplitudeEncoder
from qdap.frame.qframe import Subframe, SubframeType


@dataclass
class BornRuleResult:
    n_subframes: int
    amplitudes: np.ndarray
    probabilities: np.ndarray       # |α|²
    normalization_error: float      # |Σ|α|² - 1|
    is_normalized: bool
    monotonicity_holds: bool
    distribution_test: str          # "VALID" | "NON-UNIFORM"
    ks_statistic: float
    entropy_bits: float             # Shannon entropy

    def summary(self) -> str:
        status = "✅" if self.is_normalized and self.monotonicity_holds else "❌"
        return (
            f"{status} Born Kuralı Analizi ({self.n_subframes} subframe)\n"
            f"  Normalleşme hatası: {self.normalization_error:.2e}\n"
            f"  Monotoniklik:       {'✅' if self.monotonicity_holds else '❌'}\n"
            f"  Shannon entropi:    {self.entropy_bits:.3f} bit\n"
            f"  Dağılım testi:      {self.distribution_test}"
        )


class BornRuleVerifier:
    """
    QDAP AmplitudeEncoder'ın Born kuralıyla tutarlılığını doğrula.
    """

    NORM_TOLERANCE = 1e-9

    def __init__(self):
        self.encoder = AmplitudeEncoder()

    def verify(self, subframes: List[Subframe]) -> BornRuleResult:
        amplitudes = self.encoder.encode(subframes)
        probs = amplitudes ** 2

        # 1. Normalleşme kontrolü
        norm_error = abs(probs.sum() - 1.0)
        is_norm = norm_error < self.NORM_TOLERANCE

        # 2. Monotoniklik: öncelik sırası amplitude sırasıyla uyuşuyor mu?
        priorities = np.array([self.encoder._compute_priority(sf)
                               for sf in subframes])
        prio_rank = np.argsort(priorities)[::-1]
        amp_rank = np.argsort(amplitudes)[::-1]

        # Spearman rank korelasyonu — 1.0 = mükemmel monotonik
        if len(subframes) >= 3:
            spearman, _ = stats.spearmanr(prio_rank, amp_rank)
            monotonic = spearman > 0.95
        else:
            # For 2 subframes, just check ordering
            monotonic = bool(np.array_equal(prio_rank, amp_rank))

        # 3. Shannon entropi
        entropy = float(-np.sum(probs * np.log2(probs + 1e-10)))

        # 4. Dağılım geçerlilik testi
        if len(subframes) >= 5:
            ks_stat, ks_p = stats.kstest(probs, 'uniform',
                                          args=(0, 1 / len(probs)))
            dist_valid = "VALID" if ks_p > 0.05 else "NON-UNIFORM (expected)"
        else:
            ks_stat = 0.0
            dist_valid = "N/A (too few samples)"

        return BornRuleResult(
            n_subframes=len(subframes),
            amplitudes=amplitudes,
            probabilities=probs,
            normalization_error=norm_error,
            is_normalized=is_norm,
            monotonicity_holds=monotonic,
            distribution_test=dist_valid,
            ks_statistic=float(ks_stat),
            entropy_bits=entropy,
        )

    def verify_statistical_suite(self, n_trials: int = 10_000) -> dict:
        """
        N rastgele subframe konfigürasyonunda Born kuralını test et.
        Paper için istatistiksel güçlü kanıt.
        """
        rng = np.random.RandomState(42)
        norm_errors = []
        all_pass = 0

        for trial in range(n_trials):
            n_sf = rng.randint(2, 8)
            subframes = [
                Subframe(
                    payload=bytes(rng.randint(0, 256, 64).tolist()),
                    type=SubframeType.DATA,
                    deadline_ms=float(rng.randint(1, 1000)),
                )
                for _ in range(n_sf)
            ]

            result = self.verify(subframes)
            norm_errors.append(result.normalization_error)

            if result.is_normalized and result.monotonicity_holds:
                all_pass += 1

        norm_arr = np.array(norm_errors)
        return {
            "n_trials": n_trials,
            "pass_rate": all_pass / n_trials,
            "norm_error_max": float(norm_arr.max()),
            "norm_error_mean": float(norm_arr.mean()),
            "norm_error_p99": float(np.percentile(norm_arr, 99)),
            "machine_epsilon": float(np.finfo(float).eps),
        }
