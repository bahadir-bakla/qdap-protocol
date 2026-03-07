"""
Adaptive Markov Chain — Channel Loss Modeling
==============================================

2-state (good/bad) Gilbert-Elliott kanal modeli.
Ghost Session'ın kayıp tahminlerini besler.

States:
    good → paket başarıyla iletildi
    bad  → paket kayboldu

Geçiş olasılıkları gelen gözlemlerle adaptif güncellenir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AdaptiveMarkovChain:
    """
    2-state adaptive Markov chain for channel modeling.

    Gilbert-Elliott modeli: good ↔ bad geçişlerini
    sliding window ile öğrenir.

    Usage:
        mc = AdaptiveMarkovChain()
        mc.update('good', rtt_ms=5.2)
        mc.update('bad', rtt_ms=150.0)
        next_state = mc.predict_next()
        p_loss = mc.loss_probability(age_ms=250.0)
    """

    states: list[str] = field(default_factory=lambda: ["good", "bad"])
    initial_probs: list[float] = field(default_factory=lambda: [0.95, 0.05])

    # Transition matrix: [from_state][to_state]
    # Default: stay in good state 95% of time, bad→good 80% of time
    _transition_matrix: np.ndarray = field(init=False)
    _current_state: str = field(default="good")
    _rtt_history: list[float] = field(default_factory=list)
    _rtt_window_size: int = 50
    _observation_count: int = 0
    _good_count: int = 0
    _bad_count: int = 0

    def __post_init__(self):
        self._transition_matrix = np.array([
            [0.95, 0.05],  # good → [good, bad]
            [0.80, 0.20],  # bad  → [good, bad]
        ], dtype=np.float64)

    def predict_next(self) -> str:
        """Predict next channel state based on current state and transition probs."""
        state_idx = self.states.index(self._current_state)
        probs = self._transition_matrix[state_idx]
        return self.states[np.argmax(probs)]

    def update(self, observed_state: str, rtt_ms: float = 0.0) -> None:
        """
        Update Markov chain with an observation.

        Args:
            observed_state: 'good' or 'bad'
            rtt_ms: observed round-trip time in milliseconds
        """
        if observed_state not in self.states:
            raise ValueError(f"Unknown state: {observed_state}. Expected one of {self.states}")

        old_idx = self.states.index(self._current_state)
        new_idx = self.states.index(observed_state)

        # Adaptive transition probability update (exponential moving average)
        alpha = 0.1  # learning rate
        for j in range(len(self.states)):
            if j == new_idx:
                self._transition_matrix[old_idx][j] += alpha * (1.0 - self._transition_matrix[old_idx][j])
            else:
                self._transition_matrix[old_idx][j] *= (1.0 - alpha)

        # Normalize row
        row_sum = self._transition_matrix[old_idx].sum()
        if row_sum > 0:
            self._transition_matrix[old_idx] /= row_sum

        self._current_state = observed_state
        self._observation_count += 1

        if observed_state == "good":
            self._good_count += 1
        else:
            self._bad_count += 1

        # RTT tracking
        if rtt_ms > 0:
            self._rtt_history.append(rtt_ms)
            if len(self._rtt_history) > self._rtt_window_size:
                self._rtt_history = self._rtt_history[-self._rtt_window_size:]

    def loss_probability(self, age_ms: float) -> float:
        """
        Estimate probability that a packet of given age has been lost.

        Higher age relative to expected RTT → higher loss probability.
        """
        expected_rtt = self.expected_rtt_ms()
        if expected_rtt < 1e-9:
            return 0.5

        ratio = age_ms / expected_rtt
        # Sigmoid-like function centered at 2.5× expected RTT
        loss_prob = 1.0 / (1.0 + np.exp(-2.0 * (ratio - 2.5)))

        # Weight by current bad-state probability
        state_idx = self.states.index(self._current_state)
        bad_prob = self._transition_matrix[state_idx][1]

        return float(np.clip(loss_prob * (0.5 + bad_prob), 0.0, 1.0))

    def expected_rtt_ms(self) -> float:
        """Return expected RTT based on history."""
        if not self._rtt_history:
            return 100.0  # default 100ms
        return float(np.mean(self._rtt_history))

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def transition_matrix(self) -> np.ndarray:
        return self._transition_matrix.copy()
