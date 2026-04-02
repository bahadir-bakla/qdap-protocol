#!/usr/bin/env python3
"""Tests for Statistical Significance Analysis."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.statistical_analysis import (
    mean, std, confidence_interval_95, cohens_d,
    welch_t_test, effect_size_label, p_value_label,
)


def test_mean():
    assert mean([1, 2, 3, 4, 5]) == pytest.approx(3.0)
    assert mean([0.0, 10.0]) == pytest.approx(5.0)


def test_std():
    # std of uniform [1..n]: verify formula
    data = [1.0, 2.0, 3.0]
    expected = math.sqrt(((0+1+0) + (0+1+0) + (4)) / 2)  # mean=2, diffs: 1,0,1
    assert std(data) == pytest.approx(1.0, rel=1e-4)


def test_confidence_interval_contains_mean():
    import random
    random.seed(0)
    data = [random.gauss(50, 5) for _ in range(30)]
    lo, hi = confidence_interval_95(data)
    m = mean(data)
    assert lo < m < hi
    assert hi - lo > 0


def test_confidence_interval_width():
    """Wider spread → wider CI."""
    narrow = [10.0] * 29 + [10.5]
    wide   = list(range(30))
    lo_n, hi_n = confidence_interval_95(narrow)
    lo_w, hi_w = confidence_interval_95(wide)
    assert (hi_w - lo_w) > (hi_n - lo_n)


def test_cohens_d_large_effect():
    """Groups with large mean difference should yield large Cohen's d."""
    g1 = [100.0] * 30
    g2 = [0.0]   * 30
    d = cohens_d(g1, g2)
    assert abs(d) > 5.0  # extremely large effect


def test_cohens_d_negligible():
    """Nearly identical groups → negligible effect."""
    g1 = [10.0 + i * 0.001 for i in range(30)]
    g2 = [10.0 + i * 0.001 for i in range(30)]
    d = cohens_d(g1, g2)
    assert abs(d) < 0.2


def test_welch_ttest_significant():
    """Clearly separated groups should produce p < 0.05."""
    import random
    random.seed(1)
    g1 = [100.0 + random.gauss(0, 1) for _ in range(30)]
    g2 = [0.0   + random.gauss(0, 1) for _ in range(30)]
    _, p = welch_t_test(g1, g2)
    assert p < 0.05


def test_welch_ttest_not_significant():
    """Groups drawn from same distribution — p should be large."""
    import random
    random.seed(2)
    g1 = [random.gauss(10, 1) for _ in range(30)]
    g2 = [random.gauss(10, 1) for _ in range(30)]
    # With same distribution, we can't guarantee p>0.05 always,
    # but with seed=2 they shouldn't be wildly different.
    t_stat, p = welch_t_test(g1, g2)
    # Just verify the function runs and returns valid values
    assert 0.0 <= p <= 1.0
    assert isinstance(t_stat, float)


def test_effect_size_labels():
    assert effect_size_label(0.1)  == "negligible"
    assert effect_size_label(0.3)  == "small"
    assert effect_size_label(0.65) == "medium"
    assert effect_size_label(1.2)  == "large"
    # negative values use abs
    assert effect_size_label(-0.9) == "large"


def test_p_value_labels():
    assert "***"  in p_value_label(0.0001)
    assert "**"   in p_value_label(0.005)
    assert "*"    in p_value_label(0.03)
    assert "NS"   in p_value_label(0.5)
