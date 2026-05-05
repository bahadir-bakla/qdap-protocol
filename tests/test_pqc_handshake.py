# tests/test_pqc_handshake.py
"""
Post-Quantum Cryptography Handshake Tests
"""
import pytest
import hashlib
from qdap.security.pqc_handshake import (
    MLKEM768, MLDSASignature, HybridKeyPair,
    generate_hybrid_keypair, hybrid_encapsulate, hybrid_decapsulate,
    pqc_handshake_initiator, pqc_handshake_responder,
    SecurityMode, security_summary,
)


class TestMLKEM768:
    def test_keypair_sizes(self):
        pk, sk = MLKEM768.keypair()
        assert len(pk) == MLKEM768.PK_SIZE
        assert len(sk) == MLKEM768.SK_SIZE

    def test_encapsulate_sizes(self):
        pk, sk = MLKEM768.keypair()
        ct, ss = MLKEM768.encapsulate(pk)
        assert len(ct) == MLKEM768.CT_SIZE
        assert len(ss) == MLKEM768.SS_SIZE

    def test_shared_secret_length(self):
        pk, sk = MLKEM768.keypair()
        ct, ss_enc = MLKEM768.encapsulate(pk)
        ss_dec = MLKEM768.decapsulate(sk, ct)
        assert len(ss_dec) == 32

    def test_different_keypairs_different_secrets(self):
        pk1, sk1 = MLKEM768.keypair()
        pk2, sk2 = MLKEM768.keypair()
        _, ss1 = MLKEM768.encapsulate(pk1)
        _, ss2 = MLKEM768.encapsulate(pk2)
        assert ss1 != ss2


class TestMLDSA:
    def test_keypair_sizes(self):
        vk, sk = MLDSASignature.keypair()
        assert len(vk) == MLDSASignature.VK_SIZE
        assert len(sk) == MLDSASignature.SK_SIZE

    def test_sign_size(self):
        vk, sk = MLDSASignature.keypair()
        sig = MLDSASignature.sign(sk, b"test message")
        assert len(sig) == MLDSASignature.SIG_SIZE

    def test_different_messages_different_sigs(self):
        vk, sk = MLDSASignature.keypair()
        sig1 = MLDSASignature.sign(sk, b"msg1")
        sig2 = MLDSASignature.sign(sk, b"msg2")
        assert sig1 != sig2

    def test_different_keys_different_sigs(self):
        _, sk1 = MLDSASignature.keypair()
        _, sk2 = MLDSASignature.keypair()
        msg = b"same message"
        sig1 = MLDSASignature.sign(sk1, msg)
        sig2 = MLDSASignature.sign(sk2, msg)
        assert sig1 != sig2


class TestHybridKeyPair:
    def test_generate(self):
        kp = generate_hybrid_keypair()
        assert len(kp.classical_pk) == 32
        assert len(kp.pq_pk) == MLKEM768.PK_SIZE
        assert len(kp.sign_vk) == MLDSASignature.VK_SIZE
        assert kp.mode == SecurityMode.HYBRID

    def test_two_keypairs_different(self):
        kp1 = generate_hybrid_keypair()
        kp2 = generate_hybrid_keypair()
        assert kp1.classical_pk != kp2.classical_pk
        assert kp1.pq_pk != kp2.pq_pk


class TestHybridEncapsulation:
    def test_encapsulate_output_sizes(self):
        kp = generate_hybrid_keypair()
        classical_ct, pq_ct, ss = hybrid_encapsulate(
            kp.classical_pk, kp.pq_pk
        )
        assert len(ss) == 32
        assert len(pq_ct) == MLKEM768.CT_SIZE

    def test_shared_secret_not_zero(self):
        kp = generate_hybrid_keypair()
        _, _, ss = hybrid_encapsulate(kp.classical_pk, kp.pq_pk)
        assert ss != b"\x00" * 32

    def test_two_encapsulations_different(self):
        kp = generate_hybrid_keypair()
        _, _, ss1 = hybrid_encapsulate(kp.classical_pk, kp.pq_pk)
        _, _, ss2 = hybrid_encapsulate(kp.classical_pk, kp.pq_pk)
        # Different randomness → different shared secrets
        assert ss1 != ss2


class TestPQCHandshake:
    def test_handshake_completes(self):
        """Initiator ve responder aynı session key'e ulaşır."""
        kp_responder = generate_hybrid_keypair()

        pub_info = {
            "classical_pk": kp_responder.classical_pk,
            "pq_pk":        kp_responder.pq_pk,
            "my_sign_sk":   kp_responder.sign_sk,
            "initiator_sign_sk": kp_responder.sign_sk,
        }

        msg, init_result = pqc_handshake_initiator(pub_info, SecurityMode.HYBRID)
        resp_result = pqc_handshake_responder(kp_responder, msg)

        # Her ikisi aynı session key'e ulaşmalı
        assert init_result.session_key == resp_result.session_key

    def test_session_key_length(self):
        kp = generate_hybrid_keypair()
        pub = {"classical_pk": kp.classical_pk, "pq_pk": kp.pq_pk,
               "my_sign_sk": kp.sign_sk, "initiator_sign_sk": kp.sign_sk}
        _, result = pqc_handshake_initiator(pub)
        assert len(result.session_key) == 32

    def test_mode_hybrid(self):
        kp = generate_hybrid_keypair()
        pub = {"classical_pk": kp.classical_pk, "pq_pk": kp.pq_pk,
               "my_sign_sk": kp.sign_sk, "initiator_sign_sk": kp.sign_sk}
        _, result = pqc_handshake_initiator(pub, SecurityMode.HYBRID)
        assert result.mode == SecurityMode.HYBRID

    def test_quantum_security_bits(self):
        kp = generate_hybrid_keypair()
        pub = {"classical_pk": kp.classical_pk, "pq_pk": kp.pq_pk,
               "my_sign_sk": kp.sign_sk, "initiator_sign_sk": kp.sign_sk}
        _, result = pqc_handshake_initiator(pub)
        assert result.quantum_bits == 128
        assert result.classical_bits == 128


class TestSecuritySummary:
    def test_summary_structure(self):
        s = security_summary()
        assert "classical_algorithms" in s
        assert "post_quantum_ready" in s
        assert "qdap_status" in s
        assert "nist_standards" in s

    def test_nist_fips_mentioned(self):
        s = security_summary()
        nist = " ".join(s["nist_standards"])
        assert "203" in nist  # ML-KEM
        assert "204" in nist  # ML-DSA
        assert "205" in nist  # SLH-DSA

    def test_threat_timeline(self):
        s = security_summary()
        classical = s["classical_algorithms"]
        assert "2030" in classical["key_exchange"]
        assert "2030" in classical["signatures"]
