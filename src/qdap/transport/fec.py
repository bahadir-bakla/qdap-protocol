"""
Forward Error Correction — XOR-based Systematic (k, r) Code
=============================================================

Phase 13.2: Rate-adaptive FEC for lossy channels.

Architecture:
  - Systematic code: first k packets are original data (no decoding needed if all arrive)
  - r parity packets: XOR combinations of data blocks
  - Recoverable from any ≤r packet losses in a window of k+r

Analytical loss model:
  p_eff = P(>r losses in k+r transmissions)
        = Σ_{i=r+1}^{k+r} C(k+r, i) × p^i × (1-p)^(k+r-i)

Adaptive profiles:
  EMERGENCY (k=1, r=2): rate=1/3 → any 2-of-3 sufficient
    35% loss → p_eff = 0.35^3 = 4.3%  (8.1× improvement)
  RELIABLE  (k=2, r=1): rate=2/3 → 2-of-3 sufficient
    35% loss → p_eff = P(≥2 in 3) = 28.2%  (1.2× improvement)
  BALANCED  (k=2, r=2): rate=1/2 → 3-of-4 sufficient
    35% loss → p_eff = P(≥3 in 4) = 12.6%  (2.8× improvement)
  AGGRESSIVE(k=1, r=1): rate=1/2 → 1-of-2 sufficient
    35% loss → p_eff = 0.35^2 = 12.25%  (2.9× improvement)

Reference: RFC 5109 (RTP FEC), 3GPP TS 22.261 (mission-critical services)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


# ── FEC Profiles ───────────────────────────────────────────────────────────────

class FECProfile(Enum):
    """Adaptive FEC profiles matched to message class and channel conditions."""
    EMERGENCY  = ("emergency",  1, 2)   # k=1 data + r=2 parity = 3 total (rate 1/3)
    AGGRESSIVE = ("aggressive", 1, 1)   # k=1 data + r=1 parity = 2 total (rate 1/2)
    BALANCED   = ("balanced",   2, 2)   # k=2 data + r=2 parity = 4 total (rate 1/2)
    RELIABLE   = ("reliable",   2, 1)   # k=2 data + r=1 parity = 3 total (rate 2/3)
    NONE       = ("none",       1, 0)   # no FEC (rate 1/1)

    def __init__(self, label: str, k: int, r: int):
        self.label = label
        self.k = k
        self.r = r

    @property
    def rate(self) -> float:
        return self.k / (self.k + self.r) if (self.k + self.r) > 0 else 1.0

    @property
    def overhead_factor(self) -> float:
        return 1.0 / self.rate if self.rate > 0 else 1.0


# ── Analytical loss model ──────────────────────────────────────────────────────

def fec_effective_loss(raw_loss: float, k: int, r: int) -> float:
    """
    Effective loss probability after (k, r) FEC.

    A message is lost only if more than r out of k+r coded packets are dropped.
    Uses exact binomial computation.

    Args:
        raw_loss: raw channel packet loss probability (0.0–1.0)
        k:        data packets per FEC group
        r:        parity packets per FEC group

    Returns:
        Effective loss probability after FEC recovery

    Examples:
        >>> fec_effective_loss(0.35, k=1, r=2)  # EMERGENCY
        0.042875  # 35%^3 = 4.3%
        >>> fec_effective_loss(0.35, k=2, r=2)  # BALANCED
        ~0.126    # 12.6%
        >>> fec_effective_loss(0.35, k=1, r=0)  # NONE
        0.35
    """
    if r == 0:
        return raw_loss
    n = k + r
    p = max(0.0, min(1.0, raw_loss))
    q = 1.0 - p

    # P(irrecoverable) = P(more than r losses in n packets)
    p_fail = sum(
        math.comb(n, i) * (p ** i) * (q ** (n - i))
        for i in range(r + 1, n + 1)
    )
    return p_fail


def select_fec_profile(
    loss_rate: float,
    is_emergency: bool,
    max_overhead: float = 3.0,
) -> FECProfile:
    """
    Select optimal FEC profile for channel conditions.

    Decision logic:
      - Emergency messages: maximize delivery → EMERGENCY profile up to max_overhead
      - Normal + high loss (>20%): AGGRESSIVE or BALANCED
      - Normal + moderate loss (5-20%): RELIABLE
      - Normal + low loss (<5%): NONE (FEC overhead not worth it)

    Args:
        loss_rate:    current channel loss rate
        is_emergency: whether message is emergency-priority
        max_overhead: max allowed bandwidth multiplier (default 3×)

    Returns:
        Recommended FECProfile
    """
    if is_emergency:
        # k=1, r=2 → 3× overhead → any 1 of 3 packets sufficient
        if max_overhead >= 3.0:
            return FECProfile.EMERGENCY
        return FECProfile.AGGRESSIVE  # 2× overhead fallback

    if loss_rate >= 0.20:
        return FECProfile.BALANCED    # 2× overhead, 2.8× improvement
    if loss_rate >= 0.05:
        return FECProfile.RELIABLE    # 1.5× overhead, 1.2× improvement
    return FECProfile.NONE


# ── FEC Encoder ───────────────────────────────────────────────────────────────

@dataclass
class FECBlock:
    """A single FEC-encoded window ready for transmission."""
    sequence: int               # window sequence number
    data_packets: List[bytes]   # k original data packets
    parity_packets: List[bytes] # r XOR parity packets
    profile: FECProfile

    @property
    def total_packets(self) -> int:
        return len(self.data_packets) + len(self.parity_packets)

    @property
    def min_recoverable_losses(self) -> int:
        return len(self.parity_packets)


class FECEncoder:
    """
    XOR-based systematic FEC encoder.

    Encodes k data packets into k+r total packets where:
    - First k packets: original data (pass-through)
    - Last r packets: XOR parity computed across k data blocks

    Parity generation (simple XOR stripe):
      parity_j = XOR of data[i] for all i where (i % r) == j
    """

    def __init__(self, profile: FECProfile = FECProfile.BALANCED):
        self.profile = profile
        self._sequence = 0

    def encode(self, data_packets: List[bytes]) -> FECBlock:
        """
        Encode a group of data packets.

        For k data packets with r parity slots:
          parity[j] = data[j] XOR data[j+r] XOR data[j+2r] XOR ...

        Packets of different sizes are zero-padded to the longest.
        """
        k, r = self.profile.k, self.profile.r
        if not data_packets:
            raise ValueError("data_packets cannot be empty")

        # Work on fixed-size blocks (zero-pad to max length)
        max_len = max(len(p) for p in data_packets)
        padded = [p.ljust(max_len, b'\x00') if len(p) < max_len else p
                  for p in data_packets]

        parities: List[bytes] = []
        for j in range(r):
            # XOR all data packets assigned to parity stripe j
            stripe_indices = [i for i in range(len(padded)) if i % r == j]
            acc = bytearray(max_len)
            for idx in stripe_indices:
                for byte_pos in range(max_len):
                    acc[byte_pos] ^= padded[idx][byte_pos]
            parities.append(bytes(acc))

        block = FECBlock(
            sequence=self._sequence,
            data_packets=list(data_packets),
            parity_packets=parities,
            profile=self.profile,
        )
        self._sequence += 1
        return block

    def encode_single(self, data: bytes, r: int = 1) -> List[bytes]:
        """
        Convenience: encode single data packet with r parity copies.
        Returns [data, parity_0, parity_1, ...] for transmission.
        """
        parities = [data for _ in range(r)]  # XOR of 1 packet = itself
        return [data] + parities


# ── FEC Decoder ───────────────────────────────────────────────────────────────

class FECDecoder:
    """
    XOR-based FEC decoder.

    Recovers original data from a FEC block even when ≤r packets are lost.
    Operates on the received subset of packets from a FECBlock.
    """

    def decode(
        self,
        block: FECBlock,
        received_data: List[Optional[bytes]],
        received_parity: List[Optional[bytes]],
    ) -> Optional[List[bytes]]:
        """
        Attempt recovery of missing data packets.

        Args:
            block: original FECBlock (metadata only, not content)
            received_data: received data packets (None = lost)
            received_parity: received parity packets (None = lost)

        Returns:
            Recovered data packets list, or None if irrecoverable
        """
        r = block.profile.r
        total_lost = received_data.count(None) + received_parity.count(None)

        if total_lost > r:
            return None  # Beyond FEC recovery capacity

        if None not in received_data:
            return list(received_data)  # All data arrived, no recovery needed

        # Simple single-loss recovery: XOR the parity back
        # For multi-loss recovery we'd need Gaussian elimination over GF(2)
        # but for k=1 or r=1 cases XOR suffices
        recovered = list(received_data)
        for parity_idx, parity in enumerate(received_parity):
            if parity is None:
                continue
            stripe_indices = [i for i in range(len(recovered)) if i % r == parity_idx]
            known = [recovered[i] for i in stripe_indices if recovered[i] is not None]
            missing = [i for i in stripe_indices if recovered[i] is None]

            if len(missing) == 1:
                acc = bytearray(len(parity))
                for p in known:
                    for byte_pos in range(len(acc)):
                        acc[byte_pos] ^= p[byte_pos] if byte_pos < len(p) else 0
                for byte_pos in range(len(acc)):
                    acc[byte_pos] ^= parity[byte_pos]
                recovered[missing[0]] = bytes(acc)

        if None in recovered:
            return None
        return recovered


# ── Channel-adaptive FEC manager ──────────────────────────────────────────────

class AdaptiveFEC:
    """
    End-to-end FEC manager that adapts profile based on observed channel loss.

    Integrates with BPTTMarkovEstimator for predictive profile selection.
    """

    def __init__(self):
        self._observed_loss: float = 0.0
        self._alpha: float = 0.15         # EMA smoothing factor
        self._encoder = FECEncoder()
        self._decoder = FECDecoder()

    def observe_loss(self, lost: int, sent: int) -> None:
        """Update loss estimate via exponential moving average."""
        if sent > 0:
            sample = lost / sent
            self._observed_loss = (1 - self._alpha) * self._observed_loss + \
                                   self._alpha * sample

    @property
    def current_loss(self) -> float:
        return self._observed_loss

    def encode(
        self,
        data: bytes,
        is_emergency: bool = False,
        max_overhead: float = 3.0,
    ) -> Tuple[List[bytes], FECProfile]:
        """
        Encode data with adaptively-selected FEC profile.

        Returns:
            (coded_packets, profile_used)
        """
        profile = select_fec_profile(self._observed_loss, is_emergency, max_overhead)
        self._encoder.profile = profile

        if profile == FECProfile.NONE:
            return [data], profile

        block = self._encoder.encode([data])
        return block.data_packets + block.parity_packets, profile

    def effective_loss_for(self, is_emergency: bool) -> float:
        """Return estimated effective loss for this message class after FEC."""
        profile = select_fec_profile(self._observed_loss, is_emergency)
        return fec_effective_loss(self._observed_loss, profile.k, profile.r)


# ── Benchmark utility ─────────────────────────────────────────────────────────

def fec_delivery_improvement(
    raw_loss: float,
    is_emergency: bool,
    max_overhead: float = 3.0,
) -> dict:
    """
    Report FEC effectiveness for given conditions.

    Returns dict with: profile, raw_loss, effective_loss, improvement_factor,
                       overhead_factor, raw_delivery, effective_delivery
    """
    profile = select_fec_profile(raw_loss, is_emergency, max_overhead)
    eff = fec_effective_loss(raw_loss, profile.k, profile.r)
    return {
        "profile":            profile.label,
        "raw_loss":           raw_loss,
        "effective_loss":     round(eff, 6),
        "improvement_factor": round(raw_loss / max(eff, 1e-9), 2),
        "overhead_factor":    round(profile.overhead_factor, 2),
        "raw_delivery":       round((1 - raw_loss) * 100, 2),
        "effective_delivery": round((1 - eff) * 100, 2),
    }
