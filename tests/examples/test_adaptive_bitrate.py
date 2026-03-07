"""
Adaptive Bitrate Tests
========================
"""

import pytest
from unittest.mock import MagicMock

from examples.video.adaptive_bitrate import QDAPAdaptiveBitrate
from examples.video.media_types import VideoQuality


class TestAdaptiveBitrate:

    @pytest.fixture
    def abr(self):
        scheduler = MagicMock()
        scheduler.has_enough_data = True
        scheduler.get_spectrum_report.return_value = ""
        scheduler._last_energy_bands = {'low': 0.8, 'mid': 0.15, 'high': 0.05}
        return QDAPAdaptiveBitrate(scheduler)

    def test_good_channel_upgrades(self, abr):
        """Good channel → quality upgrade after hold period."""
        for _ in range(abr.UPGRADE_HOLD + 1):
            abr.update()
        assert abr.current_quality >= VideoQuality.HIGH

    def test_bad_channel_downgrades_fast(self, abr):
        """Bad channel → fast downgrade."""
        abr.scheduler._last_energy_bands = {'low': 0.2, 'mid': 0.3, 'high': 0.5}
        for _ in range(abr.DOWNGRADE_HOLD + 1):
            abr.update()
        assert abr.current_quality < VideoQuality.HIGH

    def test_stability_score_no_changes(self, abr):
        """Stable channel → high stability score."""
        for _ in range(20):
            abr.update()
        assert abr.stability_score() > 0.8

    def test_initial_quality_is_high(self, abr):
        assert abr.current_quality == VideoQuality.HIGH

    def test_quality_history_tracked(self, abr):
        for _ in range(5):
            abr.update()
        assert len(abr._quality_history) == 5
