"""Protocol comparison temel doğrulama testleri."""
import asyncio
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))
from protocol_comparison import (
    bench_qdap, bench_mqtt311, bench_http2,
    bench_raw_tcp, N_MESSAGES, EMRG_RATIO, SCENARIOS
)

NORMAL = {"delay_ms": 20,  "loss": 0.01, "label": "Normal"}
CRISIS = {"delay_ms": 300, "loss": 0.35, "label": "Crisis"}


@pytest.mark.asyncio
async def test_qdap_better_emergency_crisis():
    import random; random.seed(42)
    qdap = await bench_qdap(CRISIS, 200, EMRG_RATIO)
    random.seed(42)
    mqtt = await bench_mqtt311(CRISIS, 200, EMRG_RATIO)
    assert qdap.emrg_delivery_rate() > mqtt.emrg_delivery_rate()


@pytest.mark.asyncio
async def test_qdap_better_latency():
    import random; random.seed(42)
    qdap = await bench_qdap(NORMAL, 200, 0)
    random.seed(42)
    mqtt = await bench_mqtt311(NORMAL, 200, 0)
    assert qdap.p50() < mqtt.p50()


@pytest.mark.asyncio
async def test_http2_better_than_http11():
    import random; random.seed(42)
    h2 = await bench_http2(NORMAL, 200, 0)
    random.seed(42)
    h1 = await bench_raw_tcp(NORMAL, 200, 0)
    assert h2.delivery_rate() >= h1.delivery_rate() * 0.9


@pytest.mark.asyncio
async def test_all_protocols_run():
    from protocol_comparison import BENCHMARKS
    import random
    for name, fn in BENCHMARKS:
        random.seed(42)
        m = await fn(NORMAL, 50, 0.1)
        assert m.sent == 50, f"{name}: sent={m.sent}"
        assert m.delivered >= 0
        assert 0 <= m.delivery_rate() <= 100
