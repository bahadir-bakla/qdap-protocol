"""
X25519 ECDH Ephemeral Handshake.

Protokol:
  1. Client: ephemeral X25519 key pair üret
  2. Client → Server: ClientHello(client_public_key)
  3. Server: ephemeral X25519 key pair üret
  4. Server: shared_secret = X25519(server_private, client_public)
  5. Server → Client: ServerHello(server_public_key)
  6. Client: shared_secret = X25519(client_private, server_public)
  7. Her ikisi: HKDF(shared_secret) → session_key, hmac_key

Forward Secrecy: Her bağlantıda yeni ephemeral key pair.
Eski oturumların anahtarı bellekte tutulmaz.

Wire Format:
  ClientHello: [QDAP(4)][0x01(1)][0x01(1)][public_key(32)] = 38 byte
  ServerHello: [QDAP(4)][0x01(1)][0x02(1)][public_key(32)] = 38 byte
"""

import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from qdap.security.constants import (
    HANDSHAKE_MAGIC,
    HANDSHAKE_VERSION,
    AES_KEY_SIZE,
    HKDF_INFO_DATA,
    HKDF_INFO_HMAC,
)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

# Mesaj tipi byte'ları
MSG_CLIENT_HELLO = 0x01
MSG_SERVER_HELLO = 0x02

# MAGIC(4) + VERSION(1) + TYPE(1) + X25519_PUB(32) + ED25519_SIG(64) = 102 bytes
HELLO_SIZE = 4 + 1 + 1 + 32 + 64


@dataclass
class SessionKeys:
    """
    ECDH handshake sonucu türetilen oturum anahtarları.
    Her bağlantı için benzersiz — forward secrecy.
    """
    data_key:   bytes    # AES-256-GCM şifreleme anahtarı (32 byte)
    hmac_key:   bytes    # HMAC-SHA256 doğrulama anahtarı (32 byte)
    session_id: bytes    # Oturum kimliği (16 byte, logging için)

    def __repr__(self) -> str:
        return (f"SessionKeys(session_id={self.session_id.hex()[:8]}..., "
                f"data_key=*****, hmac_key=*****)")


def generate_ephemeral_keypair() -> X25519PrivateKey:
    """Yeni X25519 ephemeral private key üret."""
    return X25519PrivateKey.generate()


def serialize_public_key(private_key: X25519PrivateKey) -> bytes:
    """Private key'den 32 byte public key çıkar."""
    return private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )


def deserialize_public_key(raw: bytes) -> X25519PublicKey:
    """32 byte raw public key'i X25519PublicKey nesnesine dönüştür."""
    return X25519PublicKey.from_public_bytes(raw)


def compute_shared_secret(
    my_private_key:    X25519PrivateKey,
    peer_public_key:   X25519PublicKey,
) -> bytes:
    """X25519 Diffie-Hellman shared secret hesapla."""
    return my_private_key.exchange(peer_public_key)


def derive_session_keys(
    shared_secret: bytes,
    salt:          bytes = b"",
) -> SessionKeys:
    """HKDF ile shared_secret'tan güvenli oturum anahtarları türet."""
    data_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_DATA,
    ).derive(shared_secret)

    hmac_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_HMAC,
    ).derive(shared_secret)

    session_id = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt if salt else None,
        info=b"qdap-v1-session-id",
    ).derive(shared_secret)

    return SessionKeys(
        data_key=data_key,
        hmac_key=hmac_key,
        session_id=session_id,
    )


def build_hello_message(
    msg_type:        int,
    ephemeral_priv:  X25519PrivateKey,
    identity_priv:   Ed25519PrivateKey,
    nonce:           bytes,
) -> bytes:
    """
    Format: [MAGIC(4)][VERSION(1)][TYPE(1)][PUB(32)][SIG(64)]
    İmza = Ed25519(PUB + NONCE)
    """
    pub = serialize_public_key(ephemeral_priv)
    
    # İmzalanacak veri: Ephemeral Key (32 byte) + Nonce (16 byte)
    # Bu, ProVerif'te kanıtladığımız session binding taktiğidir
    payload_to_sign = pub + nonce
    signature = identity_priv.sign(payload_to_sign)
    
    return HANDSHAKE_MAGIC + bytes([HANDSHAKE_VERSION, msg_type]) + pub + signature


def parse_hello(data: bytes) -> tuple[int, bytes, bytes]:
    """
    Hello mesajını parse et.
    Returns: (msg_type, public_key_bytes, signature_bytes)
    """
    if len(data) < HELLO_SIZE:
        raise ValueError(f"Hello too short: {len(data)} < {HELLO_SIZE}")

    magic     = data[0:4]
    version   = data[4]
    msg_type  = data[5]
    pub_key   = data[6:38]
    signature = data[38:102]

    if magic != HANDSHAKE_MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}")
    if version != HANDSHAKE_VERSION:
        raise ValueError(f"Unsupported version: {version}")
    if msg_type not in (MSG_CLIENT_HELLO, MSG_SERVER_HELLO):
        raise ValueError(f"Unknown message type: {msg_type}")

    return msg_type, pub_key, signature


async def perform_client_handshake(
    reader:             "asyncio.StreamReader",
    writer:             "asyncio.StreamWriter",
    client_identity:    Ed25519PrivateKey,
    server_public_key:  Ed25519PublicKey,
) -> SessionKeys:
    """
    Client tarafında eCK-model mutual authenticated handshake.
    """
    import asyncio

    # Adım 1: Ephemeral üret, nonce/salt üret
    my_private  = generate_ephemeral_keypair()
    salt_client = os.urandom(16)
    
    client_hello = build_hello_message(
        msg_type=MSG_CLIENT_HELLO,
        ephemeral_priv=my_private,
        identity_priv=client_identity,
        nonce=salt_client
    )

    # Salt + ClientHello gönder
    writer.write(salt_client + client_hello)
    await writer.drain()

    # Adım 2: Server'dan Salt + ServerHello al
    salt_server = await asyncio.wait_for(
        reader.readexactly(16),
        timeout=10.0,
    )
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes, signature = parse_hello(raw)

    if msg_type != MSG_SERVER_HELLO:
        raise ValueError("Expected ServerHello, got ClientHello")

    # Adım 3: Signature doğrula (Server'ın ephemeral key'i + nonce_s)
    try:
        server_public_key.verify(signature, peer_pub_bytes + salt_server)
    except InvalidSignature:
        raise ValueError("Server authentication failed (Invalid Ed25519 Signature)")

    # Adım 4: Shared secret ve HKDF
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)
    
    # Ortak salt = client_salt ^ server_salt
    joint_salt = bytes(a ^ b for a, b in zip(salt_client, salt_server))
    session_keys = derive_session_keys(shared_secret, salt=joint_salt)

    return session_keys


async def perform_server_handshake(
    reader:             "asyncio.StreamReader",
    writer:             "asyncio.StreamWriter",
    server_identity:    Ed25519PrivateKey,
    client_public_key:  Ed25519PublicKey,
) -> SessionKeys:
    """
    Server tarafında eCK-model mutual authenticated handshake.
    """
    import asyncio

    # Adım 1: Client'dan Salt + ClientHello al
    salt_client = await asyncio.wait_for(
        reader.readexactly(16),
        timeout=10.0,
    )
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes, signature = parse_hello(raw)

    if msg_type != MSG_CLIENT_HELLO:
        raise ValueError("Expected ClientHello, got ServerHello")

    # Adım 2: Signature doğrula (Client'ın ephemeral key'i + nonce_c)
    try:
        client_public_key.verify(signature, peer_pub_bytes + salt_client)
    except InvalidSignature:
        raise ValueError("Client authentication failed (Invalid Ed25519 Signature)")

    # Adım 3: Ephemeral üret, nonce/salt üret
    my_private  = generate_ephemeral_keypair()
    salt_server = os.urandom(16)

    server_hello = build_hello_message(
        msg_type=MSG_SERVER_HELLO,
        ephemeral_priv=my_private,
        identity_priv=server_identity,
        nonce=salt_server
    )
    
    # Salt + ServerHello gönder
    writer.write(salt_server + server_hello)
    await writer.drain()

    # Adım 4: Shared secret ve HKDF
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)
    
    # Ortak salt = client_salt ^ server_salt
    joint_salt = bytes(a ^ b for a, b in zip(salt_client, salt_server))
    session_keys = derive_session_keys(shared_secret, salt=joint_salt)

    return session_keys

