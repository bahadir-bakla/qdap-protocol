"""
Scheduling Strategies
=====================

QFT Scheduler'ın trafik spektrumuna göre seçtiği üç farklı
paket gönderim stratejisi.

Strategy 1: BULK TRANSFER   — Düşük frekans baskın → throughput maximize
Strategy 2: LATENCY-FIRST   — Yüksek frekans baskın → RTT minimize
Strategy 3: ADAPTIVE HYBRID — Karma → iki kuyruğu paralel yönet
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdap.scheduler.qft_scheduler import Packet


class SchedulingStrategy(ABC):
    """Abstract base for packet scheduling strategies."""

    @abstractmethod
    def sort(self, queue: list[Packet]) -> list[Packet]:
        """Sort packet queue according to strategy."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...


@dataclass
class BulkTransferStrategy(SchedulingStrategy):
    """
    Düşük frekanslı trafik baskın → büyük chunk'lar halinde gönder.

    - TCP Nagle algoritmasını agresif kullan
    - Throughput maximize, latency ikincil
    """

    chunk_size: int = 65536

    @property
    def name(self) -> str:
        return "BULK_TRANSFER"

    def sort(self, queue: list[Packet]) -> list[Packet]:
        """Sort by size descending — bigger packets first for throughput."""
        return sorted(queue, key=lambda p: p.size_bytes, reverse=True)


@dataclass
class LatencyFirstStrategy(SchedulingStrategy):
    """
    Yüksek frekanslı trafik baskın → her paketi hemen gönder.

    - Nagle devre dışı
    - Küçük batch'ler, sık flush
    - RTT minimize
    """

    max_batch: int = 4

    @property
    def name(self) -> str:
        return "LATENCY_FIRST"

    def sort(self, queue: list[Packet]) -> list[Packet]:
        """Sort by deadline ascending — most urgent first."""
        return sorted(queue, key=lambda p: p.deadline_ms)


@dataclass
class AdaptiveHybridStrategy(SchedulingStrategy):
    """
    Karma trafik → iki kuyruğu paralel yönet.

    - Yüksek öncelikli → Latency-First kurallarıyla
    - Düşük öncelikli → Bulk kurallarıyla
    - Enerji dağılımına göre ağırlıklandır
    """

    low_weight: float = 0.5
    high_weight: float = 0.5

    @property
    def name(self) -> str:
        return "ADAPTIVE_HYBRID"

    def sort(self, queue: list[Packet]) -> list[Packet]:
        """
        Split queue by priority threshold, apply different strategies
        to each half, then interleave.
        """
        if not queue:
            return []

        # Median deadline as threshold
        deadlines = [p.deadline_ms for p in queue]
        median_deadline = sorted(deadlines)[len(deadlines) // 2]

        urgent = [p for p in queue if p.deadline_ms <= median_deadline]
        bulk = [p for p in queue if p.deadline_ms > median_deadline]

        # Urgent → latency-first (deadline ascending)
        urgent.sort(key=lambda p: p.deadline_ms)
        # Bulk → throughput-first (size descending)
        bulk.sort(key=lambda p: p.size_bytes, reverse=True)

        # Interleave: urgent packets get priority based on high_weight ratio
        result = []
        u_idx, b_idx = 0, 0
        while u_idx < len(urgent) or b_idx < len(bulk):
            # Add urgent packets proportionally
            if u_idx < len(urgent) and (
                b_idx >= len(bulk) or self.high_weight >= self.low_weight
            ):
                result.append(urgent[u_idx])
                u_idx += 1
            elif b_idx < len(bulk):
                result.append(bulk[b_idx])
                b_idx += 1

        return result
