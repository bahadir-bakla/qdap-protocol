"""Tests for AdaptiveGhostSession (Phase 11.2)."""
import time
import pytest
from src.qdap.broker.ghost_session_adaptive import (
    AdaptiveGhostSession, GhostState, NetworkType,
    NetworkTypeDetector, AICSelector, OnlineMarkovEstimator,
    NETWORK_PROFILES, StateTransition,
)


class TestNetworkProfiles:
    def test_all_profiles_exist(self):
        for nt in NetworkType:
            assert nt in NETWORK_PROFILES

    def test_critical_iot_fastest(self):
        assert NETWORK_PROFILES[NetworkType.CRITICAL_IOT].t_idle_s < \
               NETWORK_PROFILES[NetworkType.STANDARD_IOT].t_idle_s

    def test_batch_sensor_longest(self):
        assert NETWORK_PROFILES[NetworkType.BATCH_SENSOR].t_idle_s > \
               NETWORK_PROFILES[NetworkType.STANDARD_IOT].t_idle_s

    def test_all_profiles_have_rationale(self):
        for profile in NETWORK_PROFILES.values():
            assert len(profile.rationale) > 10


class TestNetworkTypeDetector:
    def test_lan_detection(self):
        assert NetworkTypeDetector.detect(1.0, 0.0001) == NetworkType.LAN

    def test_mobile_detection(self):
        assert NetworkTypeDetector.detect(60.0, 0.02, 25.0) == NetworkType.MOBILE

    def test_wan_challenged_detection(self):
        assert NetworkTypeDetector.detect(200.0, 0.20) == NetworkType.WAN_CHALLENGED

    def test_standard_iot_default(self):
        assert NetworkTypeDetector.detect(20.0, 0.01) == NetworkType.STANDARD_IOT


class TestAICSelector:
    def test_optimal_k_is_3(self):
        assert AICSelector.optimal_k({}) == 3

    def test_justification_contains_k3(self):
        j = AICSelector.justification()
        assert "k=3" in j or "optimal" in j.lower()

    def test_aic_increases_after_k3(self):
        aic = AICSelector.EMPIRICAL_AIC
        assert aic[3] < aic[4]
        assert aic[3] < aic[5]

    def test_f1_plateaus_after_k3(self):
        f1 = AICSelector.EMPIRICAL_F1
        assert f1[3] == f1[4] == f1[5]
        assert f1[2] < f1[3]


class TestOnlineMarkovEstimator:
    def test_p_d_updates(self):
        profile = NETWORK_PROFILES[NetworkType.STANDARD_IOT]
        est = OnlineMarkovEstimator(profile)
        p_d_before = est.p_d
        for _ in range(20):
            est.update(StateTransition(GhostState.ACTIVE, GhostState.GHOST))
        assert est.p_d > p_d_before

    def test_p_r_updates(self):
        profile = NETWORK_PROFILES[NetworkType.STANDARD_IOT]
        est = OnlineMarkovEstimator(profile)
        for _ in range(5):
            est.update(StateTransition(GhostState.ACTIVE, GhostState.GHOST))
        p_r_before = est.p_r
        for _ in range(10):
            est.update(StateTransition(GhostState.GHOST, GhostState.ACTIVE))
        assert est.p_r > p_r_before

    def test_params_bounded(self):
        profile = NETWORK_PROFILES[NetworkType.STANDARD_IOT]
        est = OnlineMarkovEstimator(profile)
        for _ in range(100):
            est.update(StateTransition(GhostState.ACTIVE, GhostState.GHOST))
            est.update(StateTransition(GhostState.GHOST, GhostState.ACTIVE))
        p_d, p_r, q = est.params
        assert 0 <= p_d <= 1
        assert 0 <= p_r <= 1
        assert 0 <= q <= 1


class TestAdaptiveGhostSession:
    def test_initial_state_connecting(self):
        s = AdaptiveGhostSession("test_device")
        assert s.state == GhostState.CONNECTING

    def test_data_received_activates(self):
        s = AdaptiveGhostSession("test_device")
        s.on_data_received()
        assert s.state == GhostState.ACTIVE

    def test_network_type_changes_profile(self):
        s = AdaptiveGhostSession("test_device")
        s.set_network(rtt_ms=1.0, loss_rate=0.0001)
        assert s.profile.network_type == NetworkType.LAN
        assert s.profile.t_idle_s > 30.0

    def test_keepalive_always_zero(self):
        s = AdaptiveGhostSession("test_device")
        assert s.keepalive_bytes_per_minute() == 0

    def test_current_params_has_required_keys(self):
        s = AdaptiveGhostSession("test_device")
        p = s.current_params
        for key in ["state", "t_idle_s", "t_hard_s", "k_memory", "p_d", "p_r", "q"]:
            assert key in p

    def test_critical_iot_shorter_idle(self):
        critical = AdaptiveGhostSession("icu", NetworkType.CRITICAL_IOT)
        standard = AdaptiveGhostSession("sen", NetworkType.STANDARD_IOT)
        assert critical.profile.t_idle_s < standard.profile.t_idle_s

    def test_f1_k3_better_than_k1(self):
        assert AICSelector.EMPIRICAL_F1[3] > AICSelector.EMPIRICAL_F1[1]
