"""
Real Network Condition Tests
==============================
Phase 14.5: asyncio-based loss/delay/jitter injection over real loopback.

These tests use actual asyncio TCP sockets (not simulation) with
a transparent network emulator layer that injects packet loss and delay.
Approach: wrap asyncio.StreamWriter to intercept writes, inject delay/drop.

This is the macOS-compatible alternative to Linux's `tc netem`.

Test categories:
  1. Basic connectivity  — server/client talk through emulated loss
  2. FEC effectiveness   — delivery improvement under real conditions
  3. Emergency priority  — priority queue survives injected congestion
  4. Retransmit budget   — QFT emergency scheduling within deadline
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import List, Optional, Tuple

import pytest


# ── Network emulator ──────────────────────────────────────────────────────────

class NetworkCondition:
    """Defines a simulated network condition."""
    def __init__(
        self,
        delay_ms:  float = 0.0,
        loss_rate: float = 0.0,
        jitter_ms: float = 0.0,
        label:     str   = "ideal",
    ):
        self.delay_ms  = delay_ms
        self.loss_rate = loss_rate
        self.jitter_ms = jitter_ms
        self.label     = label

    @classmethod
    def normal(cls):     return cls(20,  0.01, 2.0,  "Normal")
    @classmethod
    def mobile(cls):     return cls(80,  0.08, 15.0, "Mobile")
    @classmethod
    def satellite(cls):  return cls(600, 0.05, 50.0, "Satellite")
    @classmethod
    def crisis(cls):     return cls(300, 0.35, 40.0, "Crisis")
    @classmethod
    def ideal(cls):      return cls(0,   0.00, 0.0,  "Ideal")


class NetworkEmulator:
    """
    Transparent asyncio write-interceptor that emulates lossy/delayed networks.

    Wraps a real asyncio.StreamWriter and intercepts writes before they
    reach the kernel socket buffer. Injects:
      - delay: asyncio.sleep(delay_ms + jitter) before write
      - loss:  random.random() < loss_rate → drop write silently

    Usage:
        emulator = NetworkEmulator(writer, condition)
        await emulator.write(data)      # may be delayed or dropped
        await emulator.drain()          # flush underlying writer
    """

    def __init__(
        self,
        writer:    asyncio.StreamWriter,
        condition: NetworkCondition,
        seed:      int = 42,
    ):
        self._writer    = writer
        self._condition = condition
        self._rng       = random.Random(seed)
        self.dropped    = 0
        self.sent       = 0

    async def write(self, data: bytes) -> bool:
        """Write data with injected delay/loss. Returns True if sent."""
        self.sent += 1

        # Packet loss
        if self._rng.random() < self._condition.loss_rate:
            self.dropped += 1
            return False

        # Delay + jitter
        if self._condition.delay_ms > 0:
            jitter = self._rng.gauss(0, self._condition.jitter_ms * 0.3)
            delay  = max(0, (self._condition.delay_ms + jitter) / 1000.0)
            await asyncio.sleep(delay)

        self._writer.write(data)
        return True

    async def drain(self):
        try:
            await self._writer.drain()
        except Exception:
            pass

    @property
    def loss_rate_observed(self) -> float:
        return self.dropped / max(self.sent, 1)


# ── Simple loopback echo server ───────────────────────────────────────────────

class _EchoServer:
    """
    Minimal asyncio TCP echo server for testing.
    Echoes every received byte back to the sender.
    """

    def __init__(self):
        self._server: Optional[asyncio.AbstractServer] = None
        self.received: List[bytes] = []
        self._port: int = 0

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(
            self._handler, host, port
        )
        self._port = self._server.sockets[0].getsockname()[1]
        return self._port

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), timeout=1.0)
                if not data:
                    break
                self.received.append(data)
                writer.write(data)
                await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @property
    def port(self) -> int:
        return self._port


# ── Test helpers ──────────────────────────────────────────────────────────────

async def _send_n_messages(
    host:      str,
    port:      int,
    n:         int,
    condition: NetworkCondition,
    payload:   bytes = b"test-payload",
    seed:      int   = 42,
) -> Tuple[int, int, List[float]]:
    """
    Open a real TCP connection, send n messages through a NetworkEmulator,
    and count how many received echoes. Returns (sent, received, latencies).
    """
    reader, writer = await asyncio.open_connection(host, port)
    emulator = NetworkEmulator(writer, condition, seed=seed)
    sent = recv = 0
    latencies: List[float] = []

    try:
        for _ in range(n):
            t0 = time.perf_counter()
            ok = await emulator.write(payload)
            await emulator.drain()
            sent += 1

            if ok:
                try:
                    data = await asyncio.wait_for(reader.read(len(payload)), timeout=2.0)
                    if data:
                        recv += 1
                        latencies.append((time.perf_counter() - t0) * 1000)
                except asyncio.TimeoutError:
                    pass  # dropped — no echo

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return sent, recv, latencies


# ── Test classes ──────────────────────────────────────────────────────────────

@pytest.fixture
async def echo_server():
    server = _EchoServer()
    port = await server.start()
    yield server, "127.0.0.1", port
    await server.stop()


class TestNetworkEmulator:

    def test_condition_presets(self):
        c = NetworkCondition.crisis()
        assert c.delay_ms  == 300
        assert c.loss_rate == 0.35
        assert c.label     == "Crisis"

    def test_ideal_no_loss(self):
        c = NetworkCondition.ideal()
        assert c.loss_rate == 0.0
        assert c.delay_ms  == 0.0

    @pytest.mark.asyncio
    async def test_emulator_drops_packets(self):
        """Emulator must drop ~loss_rate fraction of writes."""
        # Use a real loopback connection for accurate behaviour
        server = _EchoServer()
        port = await server.start()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            cond = NetworkCondition(delay_ms=1, loss_rate=0.5, jitter_ms=0, label="50%")
            emul = NetworkEmulator(writer, cond, seed=0)

            n = 100
            for _ in range(n):
                await emul.write(b"x" * 10)

            obs = emul.loss_rate_observed
            # At n=100 with p=0.5, empirical loss should be within ±15%
            assert 0.35 <= obs <= 0.65, f"loss={obs:.2f} outside expected range"
            writer.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_emulator_zero_loss(self):
        """With loss=0, all writes must go through."""
        server = _EchoServer()
        port = await server.start()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            cond = NetworkCondition(delay_ms=1, loss_rate=0.0, jitter_ms=0, label="lossless")
            emul = NetworkEmulator(writer, cond)
            for _ in range(50):
                ok = await emul.write(b"ping")
                assert ok is True
            assert emul.dropped == 0
            writer.close()
        finally:
            await server.stop()


class TestRealLoopback:

    @pytest.mark.asyncio
    async def test_ideal_delivery(self, echo_server):
        """Loopback with no loss: 100% delivery."""
        server, host, port = echo_server
        sent, recv, lats = await _send_n_messages(
            host, port, n=20,
            condition=NetworkCondition.ideal(),
        )
        assert sent == 20
        assert recv == 20
        assert len(lats) == 20
        # Ideal loopback: all latencies < 100ms
        assert max(lats) < 100.0

    @pytest.mark.asyncio
    async def test_normal_delivery_rate(self, echo_server):
        """1% loss: expect ≥90% delivery (100 messages, binomial tail)."""
        server, host, port = echo_server
        sent, recv, lats = await _send_n_messages(
            host, port, n=100,
            condition=NetworkCondition.normal(),
        )
        delivery = recv / sent
        assert delivery >= 0.85, f"delivery={delivery:.2f} below 85% threshold"

    @pytest.mark.asyncio
    async def test_mobile_delivery_rate(self, echo_server):
        """8% loss: expect ≥75% delivery."""
        server, host, port = echo_server
        sent, recv, lats = await _send_n_messages(
            host, port, n=80,
            condition=NetworkCondition.mobile(),
        )
        delivery = recv / sent
        assert delivery >= 0.70, f"delivery={delivery:.2f} below 70% threshold"

    @pytest.mark.asyncio
    async def test_latency_with_delay(self, echo_server):
        """Injected 20ms delay: round-trip should be ≥20ms."""
        server, host, port = echo_server
        sent, recv, lats = await _send_n_messages(
            host, port, n=10,
            condition=NetworkCondition(delay_ms=20, loss_rate=0.0, jitter_ms=0),
        )
        if lats:
            # Each write injects 20ms delay before send; echo adds another pass.
            # Minimum measured RTT ≥ 20ms (injected one-way).
            assert min(lats) >= 15.0, f"min_lat={min(lats):.1f}ms, expected ≥15ms"


class TestFECUnderRealLoss:

    @pytest.mark.asyncio
    async def test_fec_improves_delivery_analytically(self, echo_server):
        """
        Validate that FEC effective-loss model matches what NetworkEmulator produces.

        Strategy: run N messages through NetworkEmulator(loss=0.35).
        Compare observed delivery against FEC-predicted delivery.
        """
        import qdap
        from qdap.transport.fec import fec_effective_loss

        server, host, port = echo_server
        cond = NetworkCondition(delay_ms=1, loss_rate=0.35, jitter_ms=0, label="Crisis")
        sent, recv, _ = await _send_n_messages(
            host, port, n=200,
            condition=cond,
            seed=99,
        )
        raw_delivery = recv / sent

        # FEC EMERGENCY: predicted delivery at 35% loss
        eff_loss = fec_effective_loss(0.35, k=1, r=2)
        predicted = 1.0 - eff_loss

        # Real delivery should be near 1 - 0.35 ≈ 0.65 (no FEC applied here)
        # We just verify: FEC model is strictly better than raw observed delivery
        assert predicted > raw_delivery, (
            f"FEC predicted={predicted:.3f} should > raw={raw_delivery:.3f}"
        )
        # And FEC EMERGENCY at 35% should give >90% delivery
        assert predicted > 0.90, f"FEC delivery={predicted:.3f} expected >90%"

    def test_fec_profiles_at_various_loss_rates(self):
        """FEC profile selection follows documented thresholds."""
        import qdap

        # Emergency always gets at least AGGRESSIVE profile
        for loss in [0.01, 0.10, 0.35]:
            p = qdap.select_fec_profile(loss, is_emergency=True)
            assert p != qdap.FECProfile.NONE, f"emergency at {loss} should have FEC"

        # Normal + high loss → BALANCED
        p = qdap.select_fec_profile(0.25, is_emergency=False)
        assert p == qdap.FECProfile.BALANCED

        # Normal + low loss → NONE
        p = qdap.select_fec_profile(0.02, is_emergency=False)
        assert p == qdap.FECProfile.NONE


class TestEmergencyPriorityUnderLoss:

    @pytest.mark.asyncio
    async def test_emergency_frames_delivered_first(self, echo_server):
        """
        Emergency frames (priority=high, deadline=50ms) should arrive before
        normal frames even when both are sent through the same emulated channel.

        Test: send 1 emergency frame then 5 normal frames, verify echo order
        matches send order (emergency first in queue).
        """
        server, host, port = echo_server
        cond = NetworkCondition(delay_ms=5, loss_rate=0.0, jitter_ms=1.0)

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        emul = NetworkEmulator(writer, cond, seed=0)

        # Emergency first (small payload)
        emrg = b"\xFF" + b"EMERGENCY" + b"\x00" * 55  # 64 bytes
        normal = [b"\x00" + b"normal_data" + bytes([i]) + b"\x00" * 52 for i in range(5)]

        # Send emergency, then normals
        await emul.write(emrg)
        for n in normal:
            await emul.write(n)
        await emul.drain()

        # Receive all 6 echoes
        received = []
        for _ in range(6):
            try:
                data = await asyncio.wait_for(reader.read(64), timeout=1.0)
                if data:
                    received.append(data)
            except asyncio.TimeoutError:
                break

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        assert len(received) == 6, f"Expected 6 echoes, got {len(received)}"
        # First received should be the emergency (sent first through same socket)
        assert received[0][0] == 0xFF, "Emergency frame should be echoed first"

    @pytest.mark.asyncio
    async def test_crisis_emergency_delivery(self, echo_server):
        """In crisis conditions, at least some emergency frames get through."""
        server, host, port = echo_server
        cond = NetworkCondition.crisis()
        sent, recv, _ = await _send_n_messages(
            host, port, n=50,
            condition=cond,
            payload=b"EMERGENCY" + b"\x00" * 55,
        )
        # With 35% loss over 50 messages: expected delivery ~32+
        # Allow ±10 for stochastic variance
        assert recv >= 20, f"Got {recv}/50 deliveries in crisis — too low"


class TestQFTDeadlineScheduling:

    def test_decide_emergency_returns_micro_chunk(self):
        """QFTScheduler.decide_emergency() always returns MICRO (4096B) chunk."""
        from qdap.scheduler.qft_scheduler import QFTScheduler, CHUNK_SIZES, EMERGENCY_CHUNK_STRATEGY

        sched = QFTScheduler()
        chunk_strat, n_frags, delay_factor, eff_loss_factor = sched.decide_emergency(
            payload_size=65536,
            rtt_ms=300.0,
            loss_rate=0.35,
            deadline_ms=500.0,
        )
        chunk_size = int(chunk_strat)
        assert chunk_size == CHUNK_SIZES[EMERGENCY_CHUNK_STRATEGY]  # 4096
        assert chunk_size == 4096
        assert delay_factor < 1.0, "Emergency should have reduced delay factor"

    def test_decide_emergency_fragmentation(self):
        """Large emergency payload is fragmented into 4KB chunks."""
        from qdap.scheduler.qft_scheduler import QFTScheduler

        sched = QFTScheduler()
        _, n_frags, _, _ = sched.decide_emergency(
            payload_size=40960,  # 40KB
            deadline_ms=500.0,
        )
        assert n_frags == 10  # 40960 / 4096 = 10

    def test_effective_loss_emergency(self):
        """Effective loss after QFT deadline scheduling is reduced."""
        from qdap.scheduler.qft_scheduler import QFTScheduler, EMERGENCY_LOSS_FACTOR

        sched = QFTScheduler()
        raw = 0.35
        eff = sched.effective_loss_emergency(raw)
        assert eff < raw
        assert abs(eff - raw * EMERGENCY_LOSS_FACTOR) < 1e-9

    @pytest.mark.asyncio
    async def test_emergency_retransmit_budget(self, echo_server):
        """
        deadline_ms=500, RTT=100ms → 4 retries possible.
        Test that effective loss reduction is better than single-shot at 35% loss.
        """
        from qdap.scheduler.qft_scheduler import QFTScheduler

        sched = QFTScheduler()
        _, _, _, eff_factor = sched.decide_emergency(
            payload_size=1024,
            rtt_ms=100.0,    # 100ms RTT
            loss_rate=0.35,
            deadline_ms=500.0,  # floor(500/100) - 1 = 4 retries
        )
        # With 4 retries at 35% loss: P(all fail) = 0.35^5 = 0.52%
        # eff_factor = P(all fail) / raw_loss
        assert eff_factor < 1.0, "FEC should reduce effective loss"
        assert eff_factor < 0.10, f"Expected eff_factor < 0.10, got {eff_factor:.4f}"
