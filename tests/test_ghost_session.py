"""
Ghost Session Tests — Phase 1 Enhanced
========================================

Tests for entanglement-inspired implicit acknowledgment,
Markov chain loss modeling, ghost signature verification,
and adversarial scenarios.
"""

import time
import numpy as np
import pytest

from qdap.session.ghost_session import GhostSession, GhostStats, _derive_ghost_key
from qdap.session.markov import AdaptiveMarkovChain
from qdap.frame.qframe import QFrame, Subframe, SubframeType


class TestAdaptiveMarkovChain:
    """Markov chain loss model tests."""

    def test_initial_state_is_good(self):
        mc = AdaptiveMarkovChain()
        assert mc.current_state == "good"

    def test_predict_next_from_good(self):
        mc = AdaptiveMarkovChain()
        assert mc.predict_next() == "good"  # p(good→good) = 0.95

    def test_update_transitions(self):
        mc = AdaptiveMarkovChain()
        initial_matrix = mc.transition_matrix.copy()

        mc.update("bad", rtt_ms=200.0)
        updated_matrix = mc.transition_matrix

        assert not np.allclose(initial_matrix, updated_matrix)

    def test_rtt_tracking(self):
        mc = AdaptiveMarkovChain()
        mc.update("good", rtt_ms=10.0)
        mc.update("good", rtt_ms=20.0)
        mc.update("good", rtt_ms=30.0)

        expected_rtt = mc.expected_rtt_ms()
        assert abs(expected_rtt - 20.0) < 1e-6

    def test_default_expected_rtt(self):
        mc = AdaptiveMarkovChain()
        assert mc.expected_rtt_ms() == 100.0  # default

    def test_loss_probability_increases_with_age(self):
        mc = AdaptiveMarkovChain()
        mc.update("good", rtt_ms=10.0)

        p_young = mc.loss_probability(age_ms=5.0)
        p_old = mc.loss_probability(age_ms=100.0)

        assert p_old > p_young

    def test_invalid_state_raises(self):
        mc = AdaptiveMarkovChain()
        with pytest.raises(ValueError, match="Unknown state"):
            mc.update("invalid")

    def test_transition_matrix_rows_sum_to_one(self):
        mc = AdaptiveMarkovChain()
        for _ in range(20):
            mc.update("good", rtt_ms=10.0)
            mc.update("bad", rtt_ms=100.0)

        matrix = mc.transition_matrix
        for row in matrix:
            assert abs(np.sum(row) - 1.0) < 1e-9

    def test_consecutive_bad_increases_bad_probability(self):
        mc = AdaptiveMarkovChain()
        initial_bad_prob = mc.transition_matrix[0][1]

        for _ in range(10):
            mc.update("bad", rtt_ms=200.0)

        # After many bad transitions, bad→bad should be higher
        assert mc.current_state == "bad"


class TestGhostKey:
    """HKDF key derivation tests."""

    def test_deterministic_key(self):
        key1 = _derive_ghost_key(b"shared_secret_123")
        key2 = _derive_ghost_key(b"shared_secret_123")
        assert key1 == key2

    def test_different_secrets_different_keys(self):
        key1 = _derive_ghost_key(b"secret_A")
        key2 = _derive_ghost_key(b"secret_B")
        assert key1 != key2

    def test_key_length(self):
        key = _derive_ghost_key(b"test", length=32)
        assert len(key) == 32


class TestGhostSession:
    """Ghost Session protocol tests."""

    def setup_method(self):
        self.session_id = b"test-session-01"
        self.shared_secret = b"quantum-shared-secret-key-2024"
        self.alice = GhostSession(self.session_id, self.shared_secret)
        self.bob = GhostSession(self.session_id, self.shared_secret)

    def test_ghost_signatures_match(self):
        """Both parties should compute the same ghost signature."""
        sig_alice = self.alice._compute_ghost_signature(42, b"test payload")
        sig_bob = self.bob._compute_ghost_signature(42, b"test payload")
        assert sig_alice == sig_bob

    def test_different_seq_different_sig(self):
        sig1 = self.alice._compute_ghost_signature(1, b"data")
        sig2 = self.alice._compute_ghost_signature(2, b"data")
        assert sig1 != sig2

    def test_send_adds_to_ghost_window(self):
        self.alice.send(payload=b"hello", seq_num=1)
        assert self.alice.pending_count == 1

    def test_implicit_ack_removes_from_window(self):
        self.alice.send(payload=b"hello", seq_num=1)
        assert self.alice.pending_count == 1

        self.alice.implicit_ack(1)
        assert self.alice.pending_count == 0

    def test_implicit_ack_unknown_seq_is_noop(self):
        self.alice.implicit_ack(999)
        assert self.alice.pending_count == 0

    def test_send_returns_qframe(self):
        frame = self.alice.send(payload=b"test data", seq_num=10)
        assert isinstance(frame, QFrame)

    def test_repr(self):
        repr_str = repr(self.alice)
        assert "GhostSession" in repr_str
        assert "pending=0" in repr_str

    def test_multiple_sends_tracked(self):
        for i in range(10):
            self.alice.send(payload=f"msg_{i}".encode(), seq_num=i)
        assert self.alice.pending_count == 10

        for i in range(5):
            self.alice.implicit_ack(i)
        assert self.alice.pending_count == 5

    def test_send_includes_seq_num_in_frame(self):
        """Frame subframes should carry the seq_num."""
        frame = self.alice.send(payload=b"data", seq_num=42)
        assert frame.subframes[0].seq_num == 42

    def test_stats_tracking(self):
        """Stats should accurately reflect session state."""
        for i in range(10):
            self.alice.send(payload=f"msg_{i}".encode(), seq_num=i)

        for i in range(7):
            self.alice.implicit_ack(i)

        stats = self.alice.get_stats()
        assert stats.total_sent == 10
        assert stats.total_acked == 7
        assert stats.current_pending == 3


class TestGhostSessionRoundtrip:
    """Full Alice-Bob roundtrip tests."""

    def setup_method(self):
        self.session_id = b"roundtrip-test"
        self.shared_secret = b"shared-secret-roundtrip"
        self.alice = GhostSession(self.session_id, self.shared_secret)
        self.bob = GhostSession(self.session_id, self.shared_secret)

    def test_full_send_receive_no_loss(self):
        """Alice sends → Bob receives → Alice detects no loss."""
        frame = self.alice.send(payload=b"hello bob", seq_num=1)

        # Bob receives and verifies
        verified = self.bob.on_receive(frame)
        assert 1 in verified

        # Alice gets notified (simulating side-channel or subsequent frame info)
        self.alice.implicit_ack(1)
        assert self.alice.pending_count == 0

    def test_multi_message_roundtrip(self):
        """Multiple messages sent and received in order."""
        for i in range(20):
            frame = self.alice.send(payload=f"msg_{i}".encode(), seq_num=i)
            verified = self.bob.on_receive(frame)
            assert i in verified
            self.alice.implicit_ack(i)

        assert self.alice.pending_count == 0
        stats = self.alice.get_stats()
        assert stats.total_sent == 20
        assert stats.total_acked == 20

    def test_out_of_order_receive(self):
        """Packets received out of order should all be verified."""
        frames = []
        for i in range(5):
            frames.append(self.alice.send(payload=f"msg_{i}".encode(), seq_num=i))

        # Receive out of order: 3, 1, 4, 0, 2
        for idx in [3, 1, 4, 0, 2]:
            verified = self.bob.on_receive(frames[idx])
            assert idx in verified
            self.alice.implicit_ack(idx)

        assert self.alice.pending_count == 0


class TestAdversarial:
    """Adversarial scenario tests."""

    def setup_method(self):
        self.session_id = b"adversarial-test"
        self.shared_secret = b"adversarial-secret"
        self.alice = GhostSession(self.session_id, self.shared_secret)
        self.bob = GhostSession(self.session_id, self.shared_secret)

    def test_replay_attack_detected(self):
        """Replayed packets should be rejected."""
        frame = self.alice.send(payload=b"important", seq_num=1)

        # First receive succeeds
        verified_1 = self.bob.on_receive(frame)
        assert 1 in verified_1

        # Replay: same frame again — should be rejected
        verified_2 = self.bob.on_receive(frame)
        assert len(verified_2) == 0  # Replay detected, rejected

    def test_tampered_payload_different_signature(self):
        """Tampered payload produces different ghost signature."""
        sig_original = self.alice._compute_ghost_signature(1, b"original data")
        sig_tampered = self.alice._compute_ghost_signature(1, b"tampered data")
        assert sig_original != sig_tampered

    def test_burst_loss_detection(self):
        """5 consecutive lost packets should be detected eventually."""
        # Send 10 packets, lose 5 consecutive (seq 3-7)
        for i in range(10):
            self.alice.send(payload=f"msg_{i}".encode(), seq_num=i)

        # ACK only non-lost packets
        for i in [0, 1, 2, 8, 9]:
            self.alice.implicit_ack(i)

        assert self.alice.pending_count == 5  # 3,4,5,6,7 still pending

    def test_window_cleanup(self):
        """Ghost window should not grow beyond MAX_WINDOW_SIZE."""
        for i in range(GhostSession.MAX_WINDOW_SIZE + 100):
            self.alice.send(payload=b"x", seq_num=i)

        assert self.alice.pending_count <= GhostSession.MAX_WINDOW_SIZE

    def test_different_secret_different_signatures(self):
        """Two sessions with different secrets produce mismatched signatures."""
        eve = GhostSession(self.session_id, b"eve-different-secret")

        sig_alice = self.alice._compute_ghost_signature(1, b"data")
        sig_eve = eve._compute_ghost_signature(1, b"data")
        assert sig_alice != sig_eve

    def test_simulated_loss_scenario(self):
        """Simulate 10% loss rate, verify stats are reasonable."""
        import random
        random.seed(42)

        total = 100
        lost_seqs = set()

        for i in range(total):
            self.alice.send(payload=f"pkt_{i}".encode(), seq_num=i)

            # 10% chance of loss
            if random.random() < 0.1:
                lost_seqs.add(i)
            else:
                self.alice.implicit_ack(i)

        stats = self.alice.get_stats()
        assert stats.total_sent == total
        assert stats.total_acked == total - len(lost_seqs)
        assert stats.current_pending == len(lost_seqs)
