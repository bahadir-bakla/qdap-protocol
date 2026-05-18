"""
QDAP Bridge Adapters — ara katman / drop-in layer

QDAP application layer running on top of existing V2X MAC layers.
Shows what you gain by adding QDAP to your current radio stack
without replacing any hardware.

Adapters:
  QDAPoverDSRC   — QDAP + IEEE 802.11p EDCA MAC
  QDAPoverCV2X   — QDAP + C-V2X Mode 4 PC5 sidelink

Design:
  - Both adapters inherit QDAPProtocol (same FEC, scheduler, delta encoder)
  - MAC layer parameters are taken from the real standards (same as
    DSRCProtocol / CV2XProtocol in protocols.py)
  - Original protocols.py is NOT modified — existing results are preserved

References:
  DSRC MAC  : IEEE Std 802.11p-2010, ETSI ES 202 663
  C-V2X MAC : 3GPP TS 36.213 (SPS), 3GPP TS 36.321 §5.8.1 (aperiodic TX)
  QDAP FEC  : RFC 5109, qdap_core Rust library
"""

import numpy as np
from messages import Message, Priority, MsgType
from channel import snr_to_per
from protocols import QDAPProtocol, DeliveryResult

# ─────────────────────────────────────────────────────────────────────────────
# QDAPoverDSRC — QDAP application layer on top of IEEE 802.11p EDCA MAC
# ─────────────────────────────────────────────────────────────────────────────

class QDAPoverDSRC(QDAPProtocol):
    """
    QDAP drop-in layer over DSRC/802.11p.

    MAC layer: full EDCA model (DIFS + random backoff, broadcast collision
    probability CBR²×0.6, 6 Mbps data rate, 13 µs slot time).

    QDAP adds on top:
      • Adaptive FEC   — emergency (k=1,r=2), normal (k=2,r=1)
      • Priority sched — emergency → AC_VO (CW_min=3), normal → AC_BE (CW_min=15)
      • Delta encoding — 74.4% BSM size reduction before MAC TX
      • Ghost Session  — 0-RTT reconnect (latency modeled separately)

    Comparison target: QDAPoverDSRC vs bare DSRCProtocol
    → shows application-layer gain from adding QDAP to an 802.11p stack.
    """
    name       = "QDAP+DSRC"
    color      = "#0077B6"

    # IEEE 802.11p MAC parameters
    RATE_MBPS  = 6.0
    SLOT_US    = 13.0
    DIFS_US    = 64.0
    # EDCA CW_min per access category
    CW_VO      = 3     # AC_VO — emergency (highest priority)
    CW_VI      = 7     # AC_VI — VRU / high priority
    CW_BE      = 15    # AC_BE — normal BSMs

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:

        per_base = snr_to_per(snr_db, "dsrc")      # DSRC PHY curve
        is_emerg = (msg.priority == Priority.EMERGENCY
                    or msg.msg_type == MsgType.DENM)
        is_vru   = msg.priority == Priority.HIGH

        # ── MAC collision (DSRC broadcast model, Sepulcre 2011) ───────────
        # p_col = CBR² × 0.6; QDAP priority preemption maps to EDCA AC:
        #   AC_VO (CW_min=3)  → ~0.20× collision vs AC_BE (CW_min=15)
        #   AC_VI (CW_min=7)  → ~0.47× collision vs AC_BE
        p_col_base  = min(cbr ** 2 * 0.6, 0.55)
        cw          = self.CW_VO if is_emerg else (self.CW_VI if is_vru else self.CW_BE)
        col_factor  = cw / self.CW_BE          # 0.20, 0.47, or 1.0
        p_col       = p_col_base * col_factor

        # ── Combined per-packet loss (channel + MAC collision) ────────────
        per_eff = 1.0 - (1.0 - per_base) * (1.0 - p_col)

        # ── QDAP adaptive FEC (Rust qdap_core or Python fallback) ────────
        loss   = self._observed_loss(msg.src_id)
        p_fail = self._p_fail(per_eff, loss, is_emerg)

        # ── DSRC EDCA backoff latency ─────────────────────────────────────
        bo_slots = rng.integers(0, max(1, int(cw * (1 + cbr * 2))))
        mac_ms   = (self.DIFS_US + bo_slots * self.SLOT_US) / 1000.0

        # ── QDAP delta encoding: reduce BSM payload 74.4% before TX ──────
        eff_bytes = (msg.payload_bytes * 0.256
                     if msg.msg_type == MsgType.BSM else msg.payload_bytes)
        tx_ms = eff_bytes * 8 / (self.RATE_MBPS * 1e6) * 1000

        delivered = rng.random() > p_fail
        self._update(msg.src_id, not delivered)

        if delivered:
            return DeliveryResult(True, mac_ms + tx_ms + rng.exponential(0.2), "ok")

        reason = ("channel"
                  if rng.random() < per_base / max(per_eff, 1e-9)
                  else "collision")
        return DeliveryResult(False, 0.0, reason)


# ─────────────────────────────────────────────────────────────────────────────
# QDAPoverCV2X — QDAP application layer on top of C-V2X Mode 4 MAC
# ─────────────────────────────────────────────────────────────────────────────

class QDAPoverCV2X(QDAPProtocol):
    """
    QDAP drop-in layer over C-V2X Mode 4 (PC5 sidelink).

    BSMs  — standard SPS scheduling (U[0,100ms] offset per 3GPP TS 36.213).
             QDAP FEC and delta encoding applied before SPS TX.

    DENMs — aperiodic one-shot transmission (3GPP TS 36.321 §5.8.1):
             QDAP's emergency priority triggers immediate channel access,
             bypassing the SPS pre-allocation window.
             Resource selection is random (no pre-allocation) → collision
             exposure similar to DSRC broadcast, but QDAP FEC compensates.

    Key finding: QDAP removes the structural SPS 50% deadline miss floor
    for emergency messages while preserving SPS efficiency for routine BSMs.

    Comparison target: QDAPoverCV2X vs bare CV2XProtocol
    → isolates the benefit of QDAP's emergency bypass over C-V2X.
    """
    name       = "QDAP+C-V2X"
    color      = "#7B2D8B"

    # C-V2X Mode 4 MAC parameters
    RATE_MBPS  = 4.0
    SPS_MS     = 100.0   # SPS scheduling period (3GPP TS 36.213)
    # Aperiodic TX resource collision (random sub-channel selection)
    APERIODIC_COL_BASE = 0.50   # slightly lower than DSRC (fewer simultaneous emerg TX)

    def deliver(self, msg: Message, snr_db: float, cbr: float,
                n_vehicles: int, rng: np.random.Generator) -> DeliveryResult:

        per_base = snr_to_per(snr_db, "cv2x")      # C-V2X PHY curve
        is_emerg = (msg.priority == Priority.EMERGENCY
                    or msg.msg_type == MsgType.DENM)
        is_vru   = msg.priority == Priority.HIGH

        loss = self._observed_loss(msg.src_id)

        if is_emerg or is_vru:
            # ── Aperiodic (one-shot) TX — bypass SPS for time-critical msgs ──
            # 3GPP TS 36.321 §5.8.1: UE may request one-shot resource in
            # addition to its SPS grant. QDAP emergency priority triggers this.
            # Random sub-channel → collision exposure (no pre-coordination).
            p_col_ap   = min(cbr ** 2 * self.APERIODIC_COL_BASE, 0.40)
            col_factor = 0.25 if is_emerg else 0.55   # priority preemption
            p_col      = p_col_ap * col_factor
            per_eff    = 1.0 - (1.0 - per_base) * (1.0 - p_col)

            p_fail     = self._p_fail(per_eff, loss, is_emerg)
            delivered  = rng.random() > p_fail
            self._update(msg.src_id, not delivered)

            if delivered:
                # Aperiodic access: mini-slot sensing + TX ≈ 1-3 ms
                sched_ms  = rng.exponential(1.0)
                eff_bytes = (msg.payload_bytes * 0.256
                             if msg.msg_type == MsgType.BSM else msg.payload_bytes)
                tx_ms     = eff_bytes * 8 / (self.RATE_MBPS * 1e6) * 1000
                return DeliveryResult(True,
                                      sched_ms + tx_ms + rng.exponential(0.5),
                                      "ok")
            return DeliveryResult(False, 0.0, "channel")

        else:
            # ── SPS TX — normal BSMs follow standard semi-persistent schedule ─
            p_sps   = min(0.01 + n_vehicles * 0.0014, 0.30)
            per_eff = 1.0 - (1.0 - per_base) * (1.0 - p_sps)
            p_fail  = self._p_fail(per_eff, loss, is_emerg=False)

            delivered = rng.random() > p_fail
            self._update(msg.src_id, not delivered)

            if delivered:
                sps_ms    = rng.uniform(0, self.SPS_MS)
                eff_bytes = msg.payload_bytes * 0.256   # delta encoding
                tx_ms     = eff_bytes * 8 / (self.RATE_MBPS * 1e6) * 1000
                return DeliveryResult(True,
                                      sps_ms + tx_ms + rng.exponential(1.0),
                                      "ok")
            return DeliveryResult(False, 0.0, "sps_channel")


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

BRIDGE_PROTOCOLS = [QDAPoverDSRC, QDAPoverCV2X]
