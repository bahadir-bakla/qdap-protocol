import math
import pytest
from qdap.scheduler.qft_scheduler import QFTScheduler, LR


def test_convergence_steps_formula():
    """t* = ceil(log(ε) / log(1-lr)) formülüyle örtüşmeli."""
    t = QFTScheduler.convergence_steps(0.01, LR)
    expected = math.ceil(math.log(0.01) / math.log(1 - LR))
    assert t == expected


def test_convergence_steps_lr015():
    """lr=0.15, ε=0.01 → 29 adım."""
    assert QFTScheduler.convergence_steps(0.01, 0.15) == 29


def test_convergence_steps_lr015_tight():
    """lr=0.15, ε=0.001 → 43 adım (ceil(log(0.001)/log(0.85)) = ceil(42.50) = 43)."""
    assert QFTScheduler.convergence_steps(0.001, 0.15) == 43


def test_convergence_bound_decreasing():
    """Her adımda hata üst sınırı azalmalı."""
    bounds = [QFTScheduler.convergence_bound(t) for t in range(50)]
    assert all(bounds[i] > bounds[i + 1] for i in range(49))


def test_convergence_bound_at_zero():
    """t=0'da hata sınırı initial_gap'e eşit olmalı."""
    gap = 0.8
    assert QFTScheduler.convergence_bound(0, initial_gap=gap) == pytest.approx(gap)


def test_convergence_bound_formula():
    """(1-lr)^t · initial_gap formülü doğrulanmalı."""
    lr = 0.15
    t = 10
    gap = 0.8
    expected = (1 - lr) ** t * gap
    assert QFTScheduler.convergence_bound(t, lr=lr, initial_gap=gap) == pytest.approx(expected)


def test_empirical_convergence():
    """
    Gerçek scheduler t* × 2 adımda dominant strateji ağırlığı > 0.70 olmalı.
    Sabit kanal koşulunda (RTT=200ms, loss=0.15) tek strateji öne çıkmalı.
    """
    s = QFTScheduler()
    t_star = QFTScheduler.convergence_steps(0.01)

    for _ in range(t_star * 2):
        s.decide(512, 200.0, 0.15)

    w = s.weights
    max_w = max(w)
    assert max_w > 0.70, f"Expected dominant strategy >0.70, got {max_w:.3f}"


def test_faster_than_warmup_window():
    """Teorik yakınsama 1024 warm-up pencereininden çok daha hızlı."""
    t_star = QFTScheduler.convergence_steps(0.01)
    assert t_star < 100, f"Should converge in <100 steps, needs {t_star}"


def test_convergence_steps_monotone_in_epsilon():
    """Daha küçük ε → daha fazla adım gerekir."""
    t1 = QFTScheduler.convergence_steps(0.1)
    t2 = QFTScheduler.convergence_steps(0.01)
    t3 = QFTScheduler.convergence_steps(0.001)
    assert t1 < t2 < t3


def test_convergence_steps_monotone_in_lr():
    """Daha büyük lr → daha hızlı yakınsama (daha az adım)."""
    t_slow = QFTScheduler.convergence_steps(0.01, lr=0.05)
    t_fast = QFTScheduler.convergence_steps(0.01, lr=0.30)
    assert t_fast < t_slow
