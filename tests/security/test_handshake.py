# tests/security/test_handshake.py

import os
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from qdap.security.handshake import (
    generate_ephemeral_keypair,
    serialize_public_key,
    deserialize_public_key,
    compute_shared_secret,
    derive_session_keys,
    build_hello_message,
    parse_hello,
    MSG_CLIENT_HELLO,
    MSG_SERVER_HELLO,
    HELLO_SIZE,
)


class TestX25519Handshake:

    @pytest.fixture
    def client_id(self):
        return Ed25519PrivateKey.generate()

    @pytest.fixture
    def server_id(self):
        return Ed25519PrivateKey.generate()

    def test_keypair_generation(self):
        """Her çağrıda farklı key üretilmeli."""
        k1 = generate_ephemeral_keypair()
        k2 = generate_ephemeral_keypair()
        assert serialize_public_key(k1) != serialize_public_key(k2)

    def test_public_key_size(self):
        """X25519 public key 32 byte olmalı."""
        k   = generate_ephemeral_keypair()
        pub = serialize_public_key(k)
        assert len(pub) == 32

    def test_shared_secret_symmetric(self):
        """İki taraf aynı shared secret'a ulaşmalı."""
        alice_priv = generate_ephemeral_keypair()
        bob_priv   = generate_ephemeral_keypair()

        alice_pub  = deserialize_public_key(serialize_public_key(alice_priv))
        bob_pub    = deserialize_public_key(serialize_public_key(bob_priv))

        alice_secret = compute_shared_secret(alice_priv, bob_pub)
        bob_secret   = compute_shared_secret(bob_priv, alice_pub)

        assert alice_secret == bob_secret

    def test_shared_secret_forward_secrecy(self):
        """Farklı session'lar farklı secret üretmeli."""
        a1 = generate_ephemeral_keypair()
        b1 = generate_ephemeral_keypair()
        a2 = generate_ephemeral_keypair()
        b2 = generate_ephemeral_keypair()

        s1 = compute_shared_secret(a1, deserialize_public_key(serialize_public_key(b1)))
        s2 = compute_shared_secret(a2, deserialize_public_key(serialize_public_key(b2)))

        assert s1 != s2   # Forward secrecy

    def test_session_keys_derivation(self):
        """Session keys deterministik türetilmeli."""
        secret = b"\x42" * 32
        salt   = b"\x01" * 16

        keys1 = derive_session_keys(secret, salt)
        keys2 = derive_session_keys(secret, salt)

        assert keys1.data_key  == keys2.data_key
        assert keys1.hmac_key  == keys2.hmac_key
        assert keys1.session_id == keys2.session_id

    def test_session_keys_different_salt(self):
        """Farklı salt → farklı keys."""
        secret = b"\x42" * 32
        keys1  = derive_session_keys(secret, b"\x01" * 16)
        keys2  = derive_session_keys(secret, b"\x02" * 16)
        assert keys1.data_key != keys2.data_key

    def test_hello_wire_format(self, client_id, server_id):
        """Hello mesajları doğru formatlanmalı."""
        k = generate_ephemeral_keypair()
        nonce = os.urandom(16)

        client_hello = build_hello_message(MSG_CLIENT_HELLO, k, client_id, nonce)
        server_hello = build_hello_message(MSG_SERVER_HELLO, k, server_id, nonce)

        assert len(client_hello) == HELLO_SIZE
        assert len(server_hello) == HELLO_SIZE

        msg_type, pub, sig = parse_hello(client_hello)
        assert msg_type == MSG_CLIENT_HELLO
        assert len(pub) == 32
        assert len(sig) == 64

        msg_type, pub, sig = parse_hello(server_hello)
        assert msg_type == MSG_SERVER_HELLO

    def test_full_handshake_simulation(self, client_id, server_id):
        """Alice-Bob tam handshake simülasyonu."""
        alice_priv = generate_ephemeral_keypair()
        bob_priv   = generate_ephemeral_keypair()
        
        salt_client = os.urandom(16)
        salt_server = os.urandom(16)

        # Alice ClientHello gönderir
        client_hello = build_hello_message(MSG_CLIENT_HELLO, alice_priv, client_id, salt_client)
        _, alice_pub_bytes, alice_sig = parse_hello(client_hello)
        
        # Bob doğrular
        client_id.public_key().verify(alice_sig, alice_pub_bytes + salt_client)

        # Bob ServerHello gönderir
        server_hello = build_hello_message(MSG_SERVER_HELLO, bob_priv, server_id, salt_server)
        _, bob_pub_bytes, bob_sig = parse_hello(server_hello)
        
        # Alice doğrular
        server_id.public_key().verify(bob_sig, bob_pub_bytes + salt_server)

        # Her ikisi shared secret hesaplar
        alice_secret = compute_shared_secret(
            alice_priv, deserialize_public_key(bob_pub_bytes)
        )
        bob_secret = compute_shared_secret(
            bob_priv, deserialize_public_key(alice_pub_bytes)
        )
        assert alice_secret == bob_secret

        joint_salt = bytes(a ^ b for a, b in zip(salt_client, salt_server))
        alice_keys = derive_session_keys(alice_secret, joint_salt)
        bob_keys   = derive_session_keys(bob_secret, joint_salt)

        assert alice_keys.data_key  == bob_keys.data_key
        assert alice_keys.hmac_key  == bob_keys.hmac_key
