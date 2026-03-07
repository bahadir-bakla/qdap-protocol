# src/qdap/_rust_bridge.py
"""
Rust/Python seçici köprü.

Rust build varsa → qdap_core (hızlı)
Yoksa           → pure Python fallback (mevcut implementasyon)

Kullanım:
    from qdap._rust_bridge import hash_frame, encrypt_frame, decrypt_frame
    
    # Otomatik olarak Rust veya Python kullanır
    digest = hash_frame(payload)
"""

import logging

log = logging.getLogger(__name__)

try:
    import qdap_core as _rust
    RUST_AVAILABLE = False  # Disabled temporarily to test PyO3 overhead
    log.info("qdap_core Rust backend disabled for testing")
except ImportError:
    _rust = None
    RUST_AVAILABLE = False
    log.debug("qdap_core not available — using pure Python fallback")


def hash_frame(payload: bytes) -> bytes:
    """SHA3-256 hash — Rust varsa Rust, yoksa Python."""
    if RUST_AVAILABLE:
        return _rust.hash_frame(payload)
    # Pure Python fallback
    import hashlib
    return hashlib.sha3_256(payload).digest()


def encrypt_frame(
    key:       bytes,
    nonce:     bytes,
    plaintext: bytes,
    aad:       bytes = b"",
) -> bytes:
    """
    AES-256-GCM şifreleme.
    Returns: ciphertext + 16 byte tag
    """
    if RUST_AVAILABLE:
        return _rust.encrypt_frame(key, nonce, plaintext, aad)
    # Pure Python fallback (mevcut FrameEncryptor)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).encrypt(nonce, plaintext, aad if aad else None)


def decrypt_frame(
    key:        bytes,
    nonce:      bytes,
    ciphertext: bytes,
    tag:        bytes,
    aad:        bytes = b"",
) -> bytes:
    """
    AES-256-GCM deşifreleme.
    Raises ValueError eğer authentication başarısız.
    """
    if RUST_AVAILABLE:
        return _rust.decrypt_frame(key, nonce, ciphertext, tag, aad)
    # Pure Python fallback
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, aad if aad else None)
    except InvalidTag:
        raise ValueError("Authentication failed")


def x25519_generate_keypair() -> tuple[bytes, bytes]:
    """
    X25519 ephemeral keypair üret.
    Returns: (private_key_32b, public_key_32b)
    """
    if RUST_AVAILABLE:
        return _rust.x25519_generate_keypair()
    # Pure Python fallback
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption
    )
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes  = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes, pub_bytes


def x25519_diffie_hellman(
    private_key: bytes,
    public_key:  bytes,
) -> bytes:
    """X25519 DH — shared secret hesapla."""
    if RUST_AVAILABLE:
        return _rust.x25519_diffie_hellman(private_key, public_key)
    # Pure Python fallback
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    priv = X25519PrivateKey.from_private_bytes(private_key)
    pub  = X25519PublicKey.from_public_bytes(public_key)
    return priv.exchange(pub)


def normalize_amplitudes(amplitudes: list[float]) -> list[float]:
    """L2 normalizasyon."""
    if RUST_AVAILABLE:
        return _rust.normalize_amplitudes(amplitudes)
    # Pure Python fallback
    import math
    norm = math.sqrt(sum(x * x for x in amplitudes))
    if norm < 1e-10:
        uniform = 1.0 / math.sqrt(len(amplitudes))
        return [uniform] * len(amplitudes)
    return [x / norm for x in amplitudes]


def compute_deadline_weights(deadlines_ms: list[float]) -> list[float]:
    """Deadline'lardan amplitude ağırlıkları hesapla."""
    if RUST_AVAILABLE:
        return _rust.compute_deadline_weights(deadlines_ms)
    # Pure Python fallback
    min_d = max(min(deadlines_ms), 0.001)
    raw   = [min_d / max(d, 0.001) for d in deadlines_ms]
    return normalize_amplitudes(raw)


def backend_info() -> dict:
    """Hangi backend kullanılıyor?"""
    return {
        "rust_available":   RUST_AVAILABLE,
        "backend":          "rust" if RUST_AVAILABLE else "python",
        "version":          getattr(_rust, "__version__", None) if RUST_AVAILABLE else None,
    }
