"""
Channel Trace Generator
=========================

Generates synthetic but realistic channel traces for
Ghost Session verification. Models include:
- Gilbert-Elliott (standard wireless)
- Pareto burst (heavy-tail WiFi)
- Periodic congestion
"""

from __future__ import annotations

import numpy as np
from pathlib import Path


class ChannelTraceGenerator:
    """
    Gerçek dünya kanal davranışlarını simüle et.
    """

    def gilbert_elliott(
        self,
        n: int,
        p_loss: float = 0.05,
        p_recovery: float = 0.90,
        seed: int = 42,
    ) -> np.ndarray:
        """Standart 2-state Markov kanal izi. True = kayıp."""
        rng = np.random.RandomState(seed)
        state = 0   # 0=good, 1=bad
        trace = np.zeros(n, dtype=bool)

        for i in range(n):
            if state == 0:
                if rng.random() < p_loss:
                    state = 1
            else:
                trace[i] = True
                if rng.random() < p_recovery:
                    state = 0

        return trace

    def pareto_burst(
        self,
        n: int,
        mean_loss_rate: float = 0.05,
        burst_shape: float = 1.5,
        seed: int = 7,
    ) -> np.ndarray:
        """Heavy-tail burst kayıp — WiFi ve mobil ağlara yakın."""
        rng = np.random.RandomState(seed)
        trace = np.zeros(n, dtype=bool)
        i = 0

        while i < n:
            good_len = rng.geometric(mean_loss_rate)
            i += good_len
            if i >= n:
                break

            burst_len = int(min(
                (rng.pareto(burst_shape) + 1) * 2,
                n - i
            ))
            trace[i:i + burst_len] = True
            i += burst_len

        return trace

    def periodic_congestion(
        self,
        n: int,
        period: int = 100,
        loss_window: int = 5,
    ) -> np.ndarray:
        """Periyodik tıkanma — düzenli congestion senaryosu."""
        trace = np.zeros(n, dtype=bool)
        for i in range(n):
            if (i % period) < loss_window:
                trace[i] = True
        return trace

    def save_trace(self, trace: np.ndarray, path: str) -> None:
        """Iz verisini dosyaya kaydet."""
        np.save(path, trace)

    def load_or_generate(self, path: str, **kwargs) -> np.ndarray:
        """Cache'den yükle veya üret."""
        p = Path(path)
        if p.exists():
            return np.load(path)
        trace = self.gilbert_elliott(**kwargs)
        self.save_trace(trace, path)
        return trace
