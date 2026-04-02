# tests/test_markov_bptt.py
import math
import time
import pytest
from src.qdap.broker.markov_bptt import (
    BPTTMarkovEstimator,
    MiniLSTMNetwork,
    ChannelObservation,
    sigmoid,
    tanh,
)


class TestMathHelpers:
    def test_sigmoid_bounds(self):
        assert sigmoid(0) == pytest.approx(0.5)
        assert sigmoid(100) == pytest.approx(1.0)
        assert sigmoid(-100) == pytest.approx(0.0)

    def test_tanh_bounds(self):
        assert tanh(0) == pytest.approx(0.0)
        assert abs(tanh(100) - 1.0) < 1e-6
        assert abs(tanh(-100) + 1.0) < 1e-6


class TestChannelObservation:
    def test_features_normalized(self):
        obs = ChannelObservation(
            rtt_ms=100, loss_rate=0.1,
            payload_size=1024, time_delta_s=30,
            timestamp=0.0,
        )
        features = obs.to_features()
        assert len(features) == 6
        for f in features:
            assert -1.0 <= f <= 1.0

    def test_rtt_clamped(self):
        obs = ChannelObservation(9999, 0.99, 10**7, 9999, 0.0)
        features = obs.to_features()
        assert features[0] == pytest.approx(1.0)  # rtt clamped
        assert features[1] == pytest.approx(1.0)  # loss clamped
        assert features[2] == pytest.approx(1.0)  # payload clamped


class TestMiniLSTMNetwork:
    def test_output_shape(self):
        net = MiniLSTMNetwork(seed=42)
        seq = [[0.1] * 6 for _ in range(10)]
        out = net.forward(seq)
        assert len(out) == 3

    def test_output_in_range(self):
        net = MiniLSTMNetwork(seed=42)
        seq = [[0.5] * 6 for _ in range(10)]
        p_d, p_r, q = net.forward(seq)
        assert 0 < p_d < 1
        assert 0 < p_r < 1
        assert 0 < q < 1

    def test_different_inputs_different_outputs(self):
        net = MiniLSTMNetwork(seed=42)
        seq1 = [[0.1] * 6 for _ in range(10)]
        seq2 = [[0.9] * 6 for _ in range(10)]
        out1 = net.forward(seq1)
        out2 = net.forward(seq2)
        assert out1 != out2


class TestBPTTMarkovEstimator:
    def test_warm_up_uses_ema(self):
        est = BPTTMarkovEstimator(seed=42)
        p_d, p_r, q = est.predict()
        # Warm-up'ta EMA döner
        assert p_d == pytest.approx(est._ema_p_d)

    def test_predictions_after_observations(self):
        est = BPTTMarkovEstimator(seed=42)
        for i in range(15):
            est.observe(rtt_ms=20+i, loss_rate=0.01, payload_size=1024, time_delta_s=5.0)
        p_d, p_r, q = est.predict()
        assert 0 < p_d < 1
        assert 0 < p_r < 1
        assert 0 < q < 1

    def test_ema_updates_on_target(self):
        est = BPTTMarkovEstimator(seed=42)
        ema_before = est._ema_p_d
        est.update_target(observed_p_d=1.0, observed_p_r=0.5, observed_q=0.1)
        assert est._ema_p_d > ema_before  # EMA güncellendi

    def test_training_runs(self):
        est = BPTTMarkovEstimator(seed=42)
        # Yeterli gözlem ekle
        for i in range(20):
            est.observe(20+i*2, 0.01+i*0.001, 1024, 5.0)
        # Eğitim verisi ekle
        for _ in range(60):
            est.update_target(0.05, 0.80, 0.02)
        # Eğitim çalıştı mı?
        assert len(est.loss_history) >= 1

    def test_comparison_summary_keys(self):
        est = BPTTMarkovEstimator(seed=42)
        summary = est.comparison_summary()
        for key in ["method", "ema", "lstm", "train_steps", "warmed_up"]:
            assert key in summary

    def test_high_loss_affects_prediction(self):
        """Yüksek loss → farklı prediction."""
        est1 = BPTTMarkovEstimator(seed=42)
        est2 = BPTTMarkovEstimator(seed=42)

        for i in range(15):
            est1.observe(20,   0.01,  1024, 5.0)  # düşük loss
            est2.observe(300,  0.35,  1024, 5.0)  # yüksek loss (kriz)

        p1 = est1.predict()
        p2 = est2.predict()
        # Farklı kanallar → farklı tahminler
        assert p1 != p2
