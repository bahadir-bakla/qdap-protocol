"""
V2X Protocol models.
Each protocol's deliver() returns a DeliveryResult(delivered, latency_ms, reason).

References:
  DSRC/802.11p : IEEE Std 802.11p-2010, ETSI ES 202 663
  802.11bd     : IEEE Std 802.11bd-2022 (NGV)
  C-V2X Mode 4 : 3GPP TS 36.213, 36.321 (PC5 sidelink)
  MQTT         : OASIS MQTT 3.1.1, TCP/IP over ITS-G5
  QDAP         : Quantum-Driven Adaptive Protocol (this project)
"""
import numpy as np
from dataclasses import dataclass
from typing import Optional

from messages import Message, Priority, MsgType
from channel import snr_to_per

# Use real Rust qdap_core if available; fall back to pure-Python approximation.
try:
    import qdap_core as _qcore
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


@dataclass
class DeliveryResult:
    delivered: bool
    latency_ms: float
    reason: str  # "ok" | "channel" | "collision" | "saturation" | ...


# ─────────────────────────────────────────────────────────────────────────────
# DSRC / IEEE 802.11p
# ─────────────────────────────────────────────────────────────────────────────

class DSRCProtocol:
    name = "DSRC 802.11p"
    color = "#E63946"
    DATA_RATE_MBPS = 6.0
    DIFS_US = 64.0          # DIFS = SIFS (16µs) + 2×SlotTime (2×13µs) + AIFS
    SLOT_US = 13.0          # 10 MHz channel slot time
    CW_MIN = 15             # AC_VO contention window minimum
    MAX_RANGE_M = 500.0

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per = snr_to_per(snr_db, "dsrc")

        # Broadcast collision model — empirical, based on ETSI TR 102 861 / Sepulcre 2011.
        # Unlike unicast Bianchi, broadcast has no ACK/retransmit; collisions are mild.
        # p_col ≈ CBR² × 0.6: 5% at CBR=0.3, 15% at CBR=0.5, 35% at CBR=0.75.
        p_col = min(cbr**2 * 0.6, 0.55)

        # No priority: emergency messages wait behind normal BSMs
        is_emerg = (msg.priority == Priority.EMERGENCY)
        emerg_penalty = 0.15 if is_emerg else 0.0  # HOL-like contention penalty

        p_ok = (1 - per) * (1 - p_col) * (1 - emerg_penalty)
        delivered = rng.random() < p_ok

        if delivered:
            bo = rng.integers(0, max(1, int(self.CW_MIN * (1 + cbr * 2)))) * self.SLOT_US
            tx_us = msg.payload_bytes * 8 / (self.DATA_RATE_MBPS * 1000)
            lat_ms = (self.DIFS_US + bo + tx_us) / 1000.0 + rng.exponential(0.5)
            return DeliveryResult(True, lat_ms, "ok")

        reason = "channel" if rng.random() < per / max(per + p_col, 1e-9) else "collision"
        return DeliveryResult(False, 0.0, reason)


# ─────────────────────────────────────────────────────────────────────────────
# IEEE 802.11bd (Next-Generation V2X)
# ─────────────────────────────────────────────────────────────────────────────

class IEEE80211bdProtocol:
    name = "802.11bd"
    color = "#F4A261"
    DATA_RATE_MBPS = 19.2   # 256-QAM, LDPC, 2×2 MIMO
    MAX_RANGE_M = 800.0

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per = snr_to_per(snr_db, "80211bd")

        # 802.11bd: LDPC + mid-amble → ~3dB better than DSRC → lower PER.
        # Collision model same CBR-based form but ~25% less due to better EDCA tuning.
        p_col = min(cbr**2 * 0.45, 0.45)

        p_ok = (1 - per) * (1 - p_col)
        delivered = rng.random() < p_ok

        if delivered:
            bo = rng.integers(0, max(1, int(15 * (1 + cbr * 2)))) * 9.0
            tx_us = msg.payload_bytes * 8 / (self.DATA_RATE_MBPS * 1000)
            lat_ms = (32.0 + bo + tx_us) / 1000.0 + rng.exponential(0.3)
            return DeliveryResult(True, lat_ms, "ok")

        reason = "channel" if rng.random() < per / max(per + p_col, 1e-9) else "collision"
        return DeliveryResult(False, 0.0, reason)


# ─────────────────────────────────────────────────────────────────────────────
# C-V2X Mode 4 (PC5 sidelink, LTE D2D)
# ─────────────────────────────────────────────────────────────────────────────

class CV2XProtocol:
    name = "C-V2X Mode 4"
    color = "#2A9D8F"
    DATA_RATE_MBPS = 4.0
    SPS_PERIOD_MS = 100.0   # Semi-Persistent Scheduling period
    MAX_RANGE_M = 600.0

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per = snr_to_per(snr_db, "cv2x")

        # SPS resource collision probability (grows with density)
        p_sps = min(0.01 + n_vehicles * 0.0014, 0.30)
        p_ok = (1 - per) * (1 - p_sps)
        delivered = rng.random() < p_ok

        if delivered:
            # SPS scheduling offset: 0–100 ms within current period
            sps_ms = rng.uniform(0, self.SPS_PERIOD_MS)
            tx_ms = msg.payload_bytes * 8 / (self.DATA_RATE_MBPS * 1e6) * 1000
            lat_ms = sps_ms + tx_ms + rng.exponential(1.0)
            return DeliveryResult(True, lat_ms, "ok")

        reason = "channel" if rng.random() < per else "sps_collision"
        return DeliveryResult(False, 0.0, reason)


# ─────────────────────────────────────────────────────────────────────────────
# Raw UDP (no MAC priority, no retransmission)
# ─────────────────────────────────────────────────────────────────────────────

class UDPProtocol:
    name = "UDP"
    color = "#457B9D"

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per = snr_to_per(snr_db, "udp")

        # Queue drop at high load (no backpressure, shallow buffers)
        q_drop = (
            min(max(cbr - 0.5, 0) * 3.0, 0.90) if cbr > 0.5 else 0.0
        )
        p_ok = (1 - per) * (1 - q_drop)
        delivered = rng.random() < p_ok

        if delivered:
            base = rng.exponential(2.0)
            if cbr > 0.5:
                base += rng.exponential(10.0 * cbr)
            return DeliveryResult(True, base, "ok")

        reason = "channel" if rng.random() < per else "queue_drop"
        return DeliveryResult(False, 0.0, reason)


# ─────────────────────────────────────────────────────────────────────────────
# MQTT over TCP (broker-mediated pub/sub)
# ─────────────────────────────────────────────────────────────────────────────

class MQTTProtocol:
    name = "MQTT"
    color = "#8338EC"
    BROKER_RTT_MS = 5.0   # base broker round-trip (local RSU broker)

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per = snr_to_per(snr_db, "mqtt")

        # MQTT QoS 1: 400B BSM fits in 1 TCP segment.
        # Two transmissions must succeed: PUBLISH (vehicle→broker) + PUBLISH (broker→vehicle).
        # P(at least one fails) = 1 - P(both succeed) = 1 - (1-per)^2.
        p_tcp_fail = 1 - (1 - per) ** 2
        if rng.random() < p_tcp_fail:
            return DeliveryResult(False, 0.0, "tcp_loss")

        # TCP retransmission (RTO ≈ 200 ms in V2X environment)
        n_retransmits = rng.geometric(max(1 - p_tcp_fail, 1e-6)) - 1
        retx_ms = n_retransmits * 200.0

        # Head-of-line blocking: emergency message stuck behind low-priority data
        hol_ms = 0.0
        if msg.priority == Priority.EMERGENCY and cbr > 0.3:
            if rng.random() < 0.35 * min(cbr * 2, 1.0):
                hol_ms = rng.exponential(50.0)

        # Broker queue depth proportional to load
        q_ms = rng.exponential(max(n_vehicles // 8, 1) * 1.5)

        lat_ms = self.BROKER_RTT_MS + retx_ms + hol_ms + q_ms + rng.exponential(1.0)
        # MQTT QoS 1 will eventually deliver — but may exceed V2X deadline badly
        delivered = lat_ms <= msg.deadline_ms * 8
        reason = "ok" if delivered else "timeout"
        return DeliveryResult(delivered, lat_ms, reason)


# ─────────────────────────────────────────────────────────────────────────────
# QDAP — Quantum-Driven Adaptive Protocol
# ─────────────────────────────────────────────────────────────────────────────

class QDAPProtocol:
    name = "QDAP"
    color = "#06D6A0"

    def __init__(self):
        # Per-source adaptive loss tracking: src_id -> (sent_count, lost_count)
        self._loss_obs: dict = {}

    # ── Internal helpers ──────────────────────────────────────────────────

    def _observed_loss(self, src_id: int) -> float:
        s, l = self._loss_obs.get(src_id, (1, 0))
        return l / s

    def _update(self, src_id: int, lost: bool):
        s, l = self._loss_obs.get(src_id, (0, 0))
        self._loss_obs[src_id] = (s + 1, l + (1 if lost else 0))

    def _p_fail(self, per_base: float, loss: float, is_emerg: bool) -> float:
        """Effective failure probability using Rust exact binomial or Python fallback."""
        if _RUST_AVAILABLE:
            max_oh = 4.0 if is_emerg else 2.0
            _, k, r = _qcore.fec_select_profile(max(loss, 0.01), is_emerg, max_oh)
            return _qcore.fec_effective_loss(per_base, k, r)
        # Pure-Python fallback: repetition code approximation
        if is_emerg:
            if loss < 0.05:   k = 1.5
            elif loss < 0.15: k = 2.0
            elif loss < 0.30: k = 3.0
            else:             k = 4.0
        else:
            if loss < 0.05:   k = 1.1
            elif loss < 0.15: k = 1.5
            elif loss < 0.30: k = 2.0
            else:             k = 2.5
        return per_base ** k

    # ── Main delivery method ──────────────────────────────────────────────

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:
        per_base = snr_to_per(snr_db, "qdap")
        is_emerg = (
            msg.priority == Priority.EMERGENCY
            or msg.msg_type == MsgType.DENM
        )
        is_vru = msg.priority == Priority.HIGH

        # Observed link loss for this source
        loss = self._observed_loss(msg.src_id)

        # Exact binomial via qdap_core Rust (or Python fallback)
        p_fail = self._p_fail(per_base, loss, is_emerg)

        # Priority-aware scheduler — emergency jumps queue (no HOL blocking)
        if is_emerg:
            sched_ms = rng.exponential(0.5)
        elif is_vru:
            sched_ms = rng.exponential(1.0)
        else:
            # Normal traffic slightly penalised under load, but bounded
            sched_ms = rng.exponential(2.0) * (1 + cbr * 0.5)

        # Delta-encoded BSM (74.4% size reduction per QDAP paper)
        eff_bytes = (
            msg.payload_bytes * 0.256
            if msg.msg_type == MsgType.BSM
            else msg.payload_bytes
        )
        tx_ms = eff_bytes * 8 / (20.0 * 1e6) * 1000  # 20 Mbps effective PHY

        delivered = rng.random() > p_fail
        self._update(msg.src_id, not delivered)

        if delivered:
            lat_ms = sched_ms + tx_ms + rng.exponential(0.3)
            return DeliveryResult(True, lat_ms, "ok")

        return DeliveryResult(False, 0.0, "channel")
