# src/qdap/protocol/channel_stamps.py
"""
Channel Stamps — In-Band Control Extension
===========================================
NattyNet [Arch-TCOM'26] ilhamlı: her QFrame kanal bilgisini
in-band taşır, receiver adaptasyonu hızlandırır.

QFrame header extension (14 byte):
  rtt_hint:       uint16  — ms × 10 (0.1ms precision)
  loss_hint:      uint8   — % × 2 (0.5% precision)
  strategy_idx:   uint8   — 0=MICRO, 1=SMALL, 2=MED, 3=LARGE, 4=JUMBO
  convergence:    uint16  — scheduler t* remaining steps
  flags:          uint16  — bit flags (ghost_active, emergency, 0rtt, ...)
  reserved:       6 bytes — future use

Flags:
  bit 0: GHOST_ACTIVE      — sender is in ghost session
  bit 1: EMERGENCY         — priority ≥ 1000
  bit 2: ZERO_RTT          — 0-RTT resume active
  bit 3: DELTA_ENCODED     — payload is delta compressed
  bit 4: PARALLEL_STREAM   — part of parallel stream
  bit 5: CONVERGENCE_DONE  — scheduler fully warmed up
"""

import struct
from dataclasses import dataclass


STAMP_FORMAT = "!HBBHHxxxxxx"  # network byte order, 14 bytes total
STAMP_SIZE   = 14

FLAG_GHOST_ACTIVE      = 0x0001
FLAG_EMERGENCY         = 0x0002
FLAG_ZERO_RTT          = 0x0004
FLAG_DELTA_ENCODED     = 0x0008
FLAG_PARALLEL_STREAM   = 0x0010
FLAG_CONVERGENCE_DONE  = 0x0020


@dataclass
class ChannelStamp:
    """
    In-band kanal durumu — her QFrame'de taşınır.
    NattyNet action-commit stamps analogu.
    """
    rtt_hint:      float = 20.0    # ms
    loss_hint:     float = 0.01    # 0-1
    strategy_idx:  int   = 2       # 0-4 (MICRO..JUMBO)
    convergence:   int   = 29      # kalan warm-up adımı (0=converged)
    ghost_active:  bool  = False
    emergency:     bool  = False
    zero_rtt:      bool  = False
    delta_encoded: bool  = False
    parallel:      bool  = False
    converged:     bool  = False

    def to_bytes(self) -> bytes:
        """Serialize to 14 bytes."""
        rtt_encoded  = min(int(self.rtt_hint * 10), 65535)
        loss_encoded = min(int(self.loss_hint * 200), 255)

        flags = 0
        if self.ghost_active:    flags |= FLAG_GHOST_ACTIVE
        if self.emergency:       flags |= FLAG_EMERGENCY
        if self.zero_rtt:        flags |= FLAG_ZERO_RTT
        if self.delta_encoded:   flags |= FLAG_DELTA_ENCODED
        if self.parallel:        flags |= FLAG_PARALLEL_STREAM
        if self.converged:       flags |= FLAG_CONVERGENCE_DONE

        return struct.pack(
            STAMP_FORMAT,
            rtt_encoded,
            loss_encoded,
            self.strategy_idx & 0xFF,
            min(self.convergence, 65535),
            flags,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "ChannelStamp":
        """Deserialize from 14 bytes."""
        if len(data) < STAMP_SIZE:
            return cls()  # default
        rtt_enc, loss_enc, strat, conv, flags = struct.unpack(
            STAMP_FORMAT, data[:STAMP_SIZE]
        )
        return cls(
            rtt_hint      = rtt_enc / 10.0,
            loss_hint     = loss_enc / 200.0,
            strategy_idx  = strat,
            convergence   = conv,
            ghost_active  = bool(flags & FLAG_GHOST_ACTIVE),
            emergency     = bool(flags & FLAG_EMERGENCY),
            zero_rtt      = bool(flags & FLAG_ZERO_RTT),
            delta_encoded = bool(flags & FLAG_DELTA_ENCODED),
            parallel      = bool(flags & FLAG_PARALLEL_STREAM),
            converged     = bool(flags & FLAG_CONVERGENCE_DONE),
        )

    def summary(self) -> str:
        flags = []
        if self.ghost_active:  flags.append("GHOST")
        if self.emergency:     flags.append("EMRG")
        if self.zero_rtt:      flags.append("0RTT")
        if self.converged:     flags.append("CONV")
        flag_str = "|".join(flags) or "none"
        return (
            f"RTT={self.rtt_hint:.1f}ms loss={self.loss_hint:.1%} "
            f"strat={self.strategy_idx} conv={self.convergence} [{flag_str}]"
        )


def stamp_from_scheduler(scheduler, ghost_active=False, emergency=False,
                         zero_rtt=False, delta=False, parallel=False) -> ChannelStamp:
    """
    QFT Scheduler state'inden ChannelStamp üret.
    Her gönderim öncesinde çağrılır.
    """
    try:
        weights = scheduler.weights
        best_idx = weights.index(max(weights))
        conv_remaining = max(0, scheduler.convergence_steps() - scheduler.n_decisions)
        converged = scheduler.is_warmed_up
        # Use scheduler's internal RTT/loss estimates
        rtt = getattr(scheduler, '_estimated_rtt_ms', 20.0)
        loss = getattr(scheduler, '_estimated_loss_rate', 0.01)
    except Exception:
        best_idx = 2
        conv_remaining = 29
        converged = False
        rtt = 20.0
        loss = 0.01

    return ChannelStamp(
        rtt_hint      = rtt,
        loss_hint     = loss,
        strategy_idx  = best_idx,
        convergence   = conv_remaining,
        ghost_active  = ghost_active,
        emergency     = emergency,
        zero_rtt      = zero_rtt,
        delta_encoded = delta,
        parallel      = parallel,
        converged     = converged,
    )
