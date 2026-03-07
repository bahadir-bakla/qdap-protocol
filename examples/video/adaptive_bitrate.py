"""
QFT-Based Adaptive Bitrate Controller
=========================================

Uses QFT Scheduler's spectrum analysis for bitrate decisions.
Unlike HLS/DASH which needs HTTP probe packets, QDAP ABR
reads channel quality directly from traffic frequency domain.
"""

from __future__ import annotations

from collections import deque

from examples.video.media_types import VideoQuality
from qdap.scheduler.qft_scheduler import QFTScheduler


class QDAPAdaptiveBitrate:
    """
    QFT Scheduler's spectrum → adaptive bitrate.

    Advantage over classical ABR:
    - No extra probe packets
    - Learns from traffic patterns automatically
    - Frequency-domain channel quality estimation
    """

    UPGRADE_HOLD = 10    # 10 good frames before upgrade
    DOWNGRADE_HOLD = 2   # 2 bad frames → immediate downgrade

    def __init__(self, scheduler: QFTScheduler):
        self.scheduler = scheduler
        self.current_quality = VideoQuality.HIGH
        self._good_streak = 0
        self._bad_streak = 0
        self._quality_history: deque = deque(maxlen=100)

    def update(self) -> VideoQuality:
        """Update video quality based on current traffic spectrum."""
        if not self.scheduler.has_enough_data:
            self._quality_history.append(self.current_quality)
            return self.current_quality

        bands = self.scheduler._last_energy_bands
        low_energy = bands.get('low', 0)
        jitter_proxy = bands.get('high', 0)

        channel_good = (low_energy > 0.65 and jitter_proxy < 0.08)

        if channel_good:
            self._good_streak += 1
            self._bad_streak = 0
        else:
            self._bad_streak += 1
            self._good_streak = 0

        new_quality = self.current_quality

        if self._good_streak >= self.UPGRADE_HOLD:
            if self.current_quality < VideoQuality.ULTRA:
                new_quality = VideoQuality(int(self.current_quality) + 1)
                self._good_streak = 0
        elif self._bad_streak >= self.DOWNGRADE_HOLD:
            if self.current_quality > VideoQuality.LOW:
                new_quality = VideoQuality(int(self.current_quality) - 1)
                self._bad_streak = 0

        self.current_quality = new_quality
        self._quality_history.append(new_quality)
        return new_quality

    def stability_score(self) -> float:
        """Quality transition stability: 1.0 = no changes, 0.0 = constant switching."""
        if len(self._quality_history) < 2:
            return 1.0
        changes = sum(
            1 for i in range(1, len(self._quality_history))
            if self._quality_history[i] != self._quality_history[i - 1]
        )
        return 1.0 - (changes / len(self._quality_history))
