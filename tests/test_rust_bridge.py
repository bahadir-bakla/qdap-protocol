# tests/test_rust_bridge.py
"""
Rust ve Python implementasyonlarının aynı sonucu ürettiğini doğrula.
Her fonksiyon için parity test.
"""

import os
import pytest
import hashlib
from qdap._rust_bridge import (
    hash_frame, encrypt_frame, decrypt_frame,
    x25519_generate_keypair, x25519_diffie_hellman,
    normalize_amplitudes, RUST_AVAILABLE
)


class TestHashFrame:

    def test_sha3_256_correctness(self):
        """Rust SHA3-256 == Python hashlib SHA3-256."""
        payload  = b"QDAP test payload" * 100
        expected = hashlib.sha3_256(payload).digest()
        result   = hash_frame(payload)
        assert result == expected

    def test_empty_payload(self):
        expected = hashlib.sha3_256(b"").digest()
        assert hash_frame(b"") == expected

    def test_large_payload(self):
        payload  = os.urandom(1024 * 1024)
        expected = hashlib.sha3_256(payload).digest()
        assert hash_frame(payload) == expected


class TestEncryptDecrypt:

    def test_roundtrip(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b"Hello QDAP Rust!"
        ct    = encrypt_frame(key, nonce, plain, b"")
        pt    = decrypt_frame(key, nonce, ct[:-16], ct[-16:], b"")
        assert pt == plain

    def test_aad_roundtrip(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b"payload" * 100
        aad   = b"frame-header"
        ct    = encrypt_frame(key, nonce, plain, aad)
        pt    = decrypt_frame(key, nonce, ct[:-16], ct[-16:], aad)
        assert pt == plain

    def test_tampered_ciphertext_raises(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        ct    = encrypt_frame(key, nonce, b"secret", b"")
        tampered = bytearray(ct)
        tampered[-1] ^= 0xFF
        with pytest.raises((ValueError, Exception)):
            decrypt_frame(key, nonce, bytes(tampered)[:-16], bytes(tampered)[-16:], b"")

    def test_wrong_key_raises(self):
        key1  = os.urandom(32)
        key2  = os.urandom(32)
        nonce = os.urandom(12)
        ct    = encrypt_frame(key1, nonce, b"secret", b"")
        with pytest.raises((ValueError, Exception)):
            decrypt_frame(key2, nonce, ct[:-16], ct[-16:], b"")


class TestX25519:

    def test_keypair_size(self):
        priv, pub = x25519_generate_keypair()
        assert len(priv) == 32
        assert len(pub)  == 32

    def test_dh_symmetric(self):
        """Alice ve Bob aynı shared secret'a ulaşmalı."""
        alice_priv, alice_pub = x25519_generate_keypair()
        bob_priv,   bob_pub   = x25519_generate_keypair()

        alice_secret = x25519_diffie_hellman(alice_priv, bob_pub)
        bob_secret   = x25519_diffie_hellman(bob_priv,   alice_pub)

        assert alice_secret == bob_secret
        assert len(alice_secret) == 32

    def test_different_sessions_different_secrets(self):
        a1_priv, a1_pub = x25519_generate_keypair()
        b1_priv, b1_pub = x25519_generate_keypair()
        a2_priv, a2_pub = x25519_generate_keypair()
        b2_priv, b2_pub = x25519_generate_keypair()

        s1 = x25519_diffie_hellman(a1_priv, b1_pub)
        s2 = x25519_diffie_hellman(a2_priv, b2_pub)
        assert s1 != s2   # Forward secrecy


class TestAmplitude:

    def test_l2_norm_is_one(self):
        import math
        result = normalize_amplitudes([3.0, 4.0])
        norm   = math.sqrt(sum(x*x for x in result))
        assert abs(norm - 1.0) < 1e-10

    def test_priority_ordering(self):
        from qdap._rust_bridge import compute_deadline_weights
        weights = compute_deadline_weights([2.0, 500.0])
        assert weights[0] > weights[1]   # Emergency > routine
