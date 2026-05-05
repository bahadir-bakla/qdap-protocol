# tests/test_channel_stamps.py
import pytest
from qdap.protocol.channel_stamps import (
    ChannelStamp, STAMP_SIZE, stamp_from_scheduler,
    FLAG_EMERGENCY, FLAG_GHOST_ACTIVE, FLAG_CONVERGENCE_DONE,
)


def test_stamp_size():
    s = ChannelStamp()
    assert len(s.to_bytes()) == STAMP_SIZE


def test_roundtrip_basic():
    s = ChannelStamp(rtt_hint=150.5, loss_hint=0.35,
                     strategy_idx=0, convergence=5,
                     ghost_active=True, emergency=True)
    b = s.to_bytes()
    s2 = ChannelStamp.from_bytes(b)
    assert abs(s2.rtt_hint - 150.5) < 0.2
    assert abs(s2.loss_hint - 0.35) < 0.01
    assert s2.strategy_idx == 0
    assert s2.ghost_active is True
    assert s2.emergency is True


def test_roundtrip_all_flags():
    s = ChannelStamp(
        ghost_active=True, emergency=True, zero_rtt=True,
        delta_encoded=True, parallel=True, converged=True,
    )
    s2 = ChannelStamp.from_bytes(s.to_bytes())
    assert s2.ghost_active
    assert s2.emergency
    assert s2.zero_rtt
    assert s2.delta_encoded
    assert s2.parallel
    assert s2.converged


def test_from_bytes_short():
    """Kısa veri → default stamp, exception yok."""
    s = ChannelStamp.from_bytes(b"\x00\x01")
    assert isinstance(s, ChannelStamp)


def test_rtt_precision():
    """RTT 0.1ms precision."""
    s = ChannelStamp(rtt_hint=20.3)
    s2 = ChannelStamp.from_bytes(s.to_bytes())
    assert abs(s2.rtt_hint - 20.3) < 0.15


def test_loss_precision():
    """Loss 0.5% precision."""
    s = ChannelStamp(loss_hint=0.125)
    s2 = ChannelStamp.from_bytes(s.to_bytes())
    assert abs(s2.loss_hint - 0.125) < 0.006


def test_summary_contains_info():
    s = ChannelStamp(rtt_hint=20.0, loss_hint=0.01, emergency=True)
    summary = s.summary()
    assert "20.0ms" in summary
    assert "EMRG" in summary


def test_stamp_from_scheduler():
    from qdap.scheduler.qft_scheduler import QFTScheduler
    sched = QFTScheduler()
    for _ in range(30):
        sched.decide(1024, 20.0, 0.01)
    stamp = stamp_from_scheduler(sched, emergency=True)
    assert 0 <= stamp.strategy_idx <= 4
    assert stamp.emergency is True
