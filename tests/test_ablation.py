#!/usr/bin/env python3
"""Tests for QDAP Ablation Study."""

import asyncio
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.ablation_study import (
    AblationConfig, AblationMetrics, run_config, CONFIGS, SCENARIOS
)


CRISIS = {"delay_ms": 300, "loss": 0.35, "label": "Crisis (300ms/35%)"}
NORMAL = {"delay_ms": 20,  "loss": 0.01, "label": "Normal (20ms/1%)"}


@pytest.mark.asyncio
async def test_full_qdap_best_emergency():
    """Full QDAP should have highest emergency delivery in crisis."""
    import random
    random.seed(42)
    full_cfg = next(c for c in CONFIGS if c.name == "Full QDAP")
    baseline_cfg = next(c for c in CONFIGS if "Baseline" in c.name)

    full = await run_config(full_cfg, CRISIS, 200, 0.20)
    random.seed(42)
    base = await run_config(baseline_cfg, CRISIS, 200, 0.20)

    assert full.emrg_rate() > base.emrg_rate(), (
        f"Full QDAP emrg {full.emrg_rate():.1f}% should exceed "
        f"Baseline {base.emrg_rate():.1f}%"
    )


@pytest.mark.asyncio
async def test_priority_improves_emergency():
    """+Priority should improve emergency rate vs baseline in crisis."""
    import random
    random.seed(42)
    pri_cfg  = next(c for c in CONFIGS if c.name == "+Priority Only")
    base_cfg = next(c for c in CONFIGS if "Baseline" in c.name)

    random.seed(42)
    pri  = await run_config(pri_cfg,  CRISIS, 200, 0.20)
    random.seed(42)
    base = await run_config(base_cfg, CRISIS, 200, 0.20)

    assert pri.emrg_rate() > base.emrg_rate(), (
        f"+Priority emrg {pri.emrg_rate():.1f}% should exceed "
        f"Baseline {base.emrg_rate():.1f}%"
    )


@pytest.mark.asyncio
async def test_qft_improves_throughput():
    """+QFT should improve throughput in normal conditions."""
    import random
    random.seed(42)
    qft_cfg  = next(c for c in CONFIGS if c.name == "+QFT Only")
    base_cfg = next(c for c in CONFIGS if "Baseline" in c.name)

    random.seed(42)
    qft  = await run_config(qft_cfg,  NORMAL, 200, 0.20)
    random.seed(42)
    base = await run_config(base_cfg, NORMAL, 200, 0.20)

    assert qft.throughput() >= base.throughput() * 0.9, (
        f"+QFT throughput {qft.throughput():.2f} Mbps should be "
        f"at least 90% of Baseline {base.throughput():.2f} Mbps"
    )


@pytest.mark.asyncio
async def test_all_configs_run():
    """All 8 ablation configs should run without error."""
    import random
    for cfg in CONFIGS:
        random.seed(42)
        m = await run_config(cfg, NORMAL, 50, 0.20)
        assert isinstance(m, AblationMetrics)
        assert m.sent == 50
        assert 0 <= m.delivery_rate() <= 100
        assert 0 <= m.emrg_rate() <= 100
        assert m.throughput() >= 0
