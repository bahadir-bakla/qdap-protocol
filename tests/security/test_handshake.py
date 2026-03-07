# tests/security/test_handshake.py

import pytest
from qdap.security.handshake import (
    generate_ephemeral_keypair,
    serialize_public_key,
    deserialize_public_key,
    compute_shared_secret,
    derive_session_keys,
    build_client_hello,
    build_server_hello,
    parse_hello,
    MSG_CLIENT_HELLO,
    MSG_SERVER_HELLO,
    HELLO_SIZE,
)


class TestX25519Handshake:

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

    def test_hello_wire_format(self):
        """Hello mesajları doğru formatlanmalı."""
        k = generate_ephemeral_keypair()

        client_hello = build_client_hello(k)
        server_hello = build_server_hello(k)

        assert len(client_hello) == HELLO_SIZE
        assert len(server_hello) == HELLO_SIZE

        msg_type, pub = parse_hello(client_hello)
        assert msg_type == MSG_CLIENT_HELLO
        assert len(pub) == 32

        msg_type, pub = parse_hello(server_hello)
        assert msg_type == MSG_SERVER_HELLO

    def test_full_handshake_simulation(self):
        """Alice-Bob tam handshake simülasyonu."""
        alice_priv = generate_ephemeral_keypair()
        bob_priv   = generate_ephemeral_keypair()

        # Alice ClientHello gönderir
        client_hello = build_client_hello(alice_priv)
        _, alice_pub_bytes = parse_hello(client_hello)

        # Bob ServerHello gönderir
        server_hello = build_server_hello(bob_priv)
        _, bob_pub_bytes = parse_hello(server_hello)

        # Her ikisi shared secret hesaplar
        salt = b"\xAB" * 16
        alice_secret = compute_shared_secret(
            alice_priv, deserialize_public_key(bob_pub_bytes)
        )
        bob_secret = compute_shared_secret(
            bob_priv, deserialize_public_key(alice_pub_bytes)
        )
        assert alice_secret == bob_secret

        alice_keys = derive_session_keys(alice_secret, salt)
        bob_keys   = derive_session_keys(bob_secret, salt)

        assert alice_keys.data_key  == bob_keys.data_key
        assert alice_keys.hmac_key  == bob_keys.hmac_key
