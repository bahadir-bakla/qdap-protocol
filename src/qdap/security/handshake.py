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

# Mesaj tipi byte'ları
MSG_CLIENT_HELLO = 0x01
MSG_SERVER_HELLO = 0x02

HELLO_SIZE = 4 + 1 + 1 + 32   # MAGIC + VERSION + MSG_TYPE + PUBLIC_KEY = 38


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
    """
    Yeni X25519 ephemeral private key üret.
    Her çağrıda farklı key — ephemeral = tek kullanımlık.
    """
    return X25519PrivateKey.generate()


def serialize_public_key(private_key: X25519PrivateKey) -> bytes:
    """Private key'den 32 byte public key çıkar."""
    return private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )


def deserialize_public_key(raw: bytes) -> X25519PublicKey:
    """32 byte raw public key'i X25519PublicKey nesnesine dönüştür."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    return X25519PublicKey.from_public_bytes(raw)


def compute_shared_secret(
    my_private_key:    X25519PrivateKey,
    peer_public_key:   X25519PublicKey,
) -> bytes:
    """
    X25519 Diffie-Hellman shared secret hesapla.
    Her iki taraf da aynı 32 byte secret'a ulaşır.
    Bu secret'ı doğrudan anahtar olarak KULLANMA — HKDF'e ver.
    """
    return my_private_key.exchange(peer_public_key)


def derive_session_keys(
    shared_secret: bytes,
    salt:          bytes = b"",
) -> SessionKeys:
    """
    HKDF ile shared_secret'tan güvenli oturum anahtarları türet.

    Neden HKDF?
      X25519 çıktısı doğrudan AES anahtarı olarak kullanılamaz —
      dağılımı uniform değil. HKDF bunu düzeltir.

    Args:
        shared_secret: X25519 exchange çıktısı (32 byte)
        salt:          Rastgele salt (replay protection için)

    Returns:
        SessionKeys(data_key, hmac_key, session_id)
    """
    # Data encryption key
    data_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_DATA,
    ).derive(shared_secret)

    # HMAC key
    hmac_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_HMAC,
    ).derive(shared_secret)

    # Session ID (logging/debugging için — güvenlik değeri yok)
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


def build_client_hello(private_key: X25519PrivateKey) -> bytes:
    """
    ClientHello mesajı oluştur.
    Format: [QDAP(4)][0x01(1)][0x01(1)][public_key(32)] = 38 byte
    """
    pub = serialize_public_key(private_key)
    return HANDSHAKE_MAGIC + bytes([HANDSHAKE_VERSION, MSG_CLIENT_HELLO]) + pub


def build_server_hello(private_key: X25519PrivateKey) -> bytes:
    """
    ServerHello mesajı oluştur.
    Format: [QDAP(4)][0x01(1)][0x02(1)][public_key(32)] = 38 byte
    """
    pub = serialize_public_key(private_key)
    return HANDSHAKE_MAGIC + bytes([HANDSHAKE_VERSION, MSG_SERVER_HELLO]) + pub


def parse_hello(data: bytes) -> tuple[int, bytes]:
    """
    Hello mesajını parse et.
    Returns: (msg_type, public_key_bytes)
    Raises: ValueError eğer format yanlışsa
    """
    if len(data) < HELLO_SIZE:
        raise ValueError(f"Hello too short: {len(data)} < {HELLO_SIZE}")

    magic    = data[0:4]
    version  = data[4]
    msg_type = data[5]
    pub_key  = data[6:38]

    if magic != HANDSHAKE_MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}")

    if version != HANDSHAKE_VERSION:
        raise ValueError(f"Unsupported version: {version}")

    if msg_type not in (MSG_CLIENT_HELLO, MSG_SERVER_HELLO):
        raise ValueError(f"Unknown message type: {msg_type}")

    return msg_type, pub_key


async def perform_client_handshake(
    reader: "asyncio.StreamReader",
    writer: "asyncio.StreamWriter",
) -> SessionKeys:
    """
    Client tarafında handshake gerçekleştir.

    1. Ephemeral key pair üret
    2. ClientHello gönder
    3. ServerHello bekle
    4. Shared secret hesapla
    5. Session keys türet

    Returns: SessionKeys
    """
    import asyncio

    # Adım 1 + 2: Key pair üret ve gönder
    my_private  = generate_ephemeral_keypair()
    salt        = os.urandom(16)
    client_hello = build_client_hello(my_private)

    # Salt'ı hello'dan önce gönder (replay protection)
    writer.write(salt + client_hello)
    await writer.drain()

    # Adım 3: ServerHello al
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes = parse_hello(raw)

    if msg_type != MSG_SERVER_HELLO:
        raise ValueError("Expected ServerHello, got ClientHello")

    # Adım 4 + 5: Shared secret ve session keys
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)
    session_keys   = derive_session_keys(shared_secret, salt)

    return session_keys


async def perform_server_handshake(
    reader: "asyncio.StreamReader",
    writer: "asyncio.StreamWriter",
) -> SessionKeys:
    """
    Server tarafında handshake gerçekleştir.

    1. ClientHello + salt bekle
    2. Ephemeral key pair üret
    3. Shared secret hesapla
    4. ServerHello gönder
    5. Session keys türet

    Returns: SessionKeys
    """
    import asyncio

    # Adım 1: Salt + ClientHello al
    salt = await asyncio.wait_for(
        reader.readexactly(16),
        timeout=10.0,
    )
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes = parse_hello(raw)

    if msg_type != MSG_CLIENT_HELLO:
        raise ValueError("Expected ClientHello, got ServerHello")

    # Adım 2 + 3: Key pair üret, shared secret hesapla
    my_private     = generate_ephemeral_keypair()
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)

    # Adım 4: ServerHello gönder
    server_hello = build_server_hello(my_private)
    writer.write(server_hello)
    await writer.drain()

    # Adım 5: Session keys türet
    session_keys = derive_session_keys(shared_secret, salt)

    return session_keys
