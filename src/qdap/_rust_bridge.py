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
    RUST_AVAILABLE = True
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


# QFrame
def qframe_serialize(
    payload: bytes,
    priority: int = 0,
    deadline_ms: float = 500.0,
    sequence_number: int = 0,
    frame_type: int = 0,
) -> bytes:
    if RUST_AVAILABLE:
        return _rust.qframe_serialize(
            payload, priority, deadline_ms, sequence_number, frame_type
        )
    return _python_qframe_serialize(
        payload, priority, deadline_ms, sequence_number, frame_type
    )


def qframe_deserialize(data: bytes) -> tuple:
    """Returns: (payload, priority, deadline_ms, seq_num, frame_type, hash_valid)"""
    if RUST_AVAILABLE:
        return _rust.qframe_deserialize(data)
    return _python_qframe_deserialize(data)


def qframe_peek_header(data: bytes) -> tuple:
    """Returns: (payload_length, priority, deadline_ms, frame_type)"""
    if RUST_AVAILABLE:
        return _rust.qframe_peek_header(data)
    return _python_qframe_peek_header(data)


def _python_qframe_serialize(
    payload: bytes, priority: int, deadline_ms: float, sequence_number: int, frame_type: int
) -> bytes:
    import struct
    import hashlib
    MAGIC = 0x51444150
    payload_len = len(payload)
    hash_val = hashlib.sha3_256(payload).digest()
    
    header = bytearray(60)
    header[0:4] = struct.pack(">I", MAGIC)
    header[4] = 1
    header[5] = frame_type
    header[6:8] = struct.pack("<H", priority)
    header[8:16] = struct.pack("<d", deadline_ms)
    header[16:24] = struct.pack("<Q", sequence_number)
    header[24:28] = struct.pack("<I", payload_len)
    header[28:60] = hash_val
    return bytes(header) + payload


def _python_qframe_deserialize(data: bytes) -> tuple:
    import struct
    import hashlib
    HEADER_SIZE = 60
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Frame too short: {len(data)} < {HEADER_SIZE}")
    magic = struct.unpack(">I", data[0:4])[0]
    if magic != 0x51444150:
        raise ValueError(f"Invalid magic: 0x{magic:08X}")
    frame_type = data[5]
    priority = struct.unpack("<H", data[6:8])[0]
    deadline_ms = struct.unpack("<d", data[8:16])[0]
    seq_num = struct.unpack("<Q", data[16:24])[0]
    payload_length = struct.unpack("<I", data[24:28])[0]
    stored_hash = data[28:60]
    expected_total = HEADER_SIZE + payload_length
    if len(data) < expected_total:
        raise ValueError(f"Truncated frame: expected {expected_total} bytes, got {len(data)}")
    payload = data[HEADER_SIZE : expected_total]
    computed_hash = hashlib.sha3_256(payload).digest()
    hash_valid = computed_hash == stored_hash
    return (payload, priority, deadline_ms, seq_num, frame_type, hash_valid)


def _python_qframe_peek_header(data: bytes) -> tuple:
    import struct
    HEADER_SIZE = 60
    if len(data) < HEADER_SIZE:
        raise ValueError("Too short for header")
    magic = struct.unpack(">I", data[0:4])[0]
    if magic != 0x51444150:
        raise ValueError("Invalid magic")
    frame_type = data[5]
    priority = struct.unpack("<H", data[6:8])[0]
    deadline_ms = struct.unpack("<d", data[8:16])[0]
    payload_length = struct.unpack("<I", data[24:28])[0]
    return (payload_length, priority, deadline_ms, frame_type)


# QFT Scheduler
def qft_decide(
    payload_size: int,
    rtt_ms: float = 20.0,
    loss_rate: float = 0.01,
) -> tuple:
    """Returns: (chunk_size_bytes, strategy_index, confidence)"""
    if RUST_AVAILABLE:
        return _rust.qft_decide(payload_size, rtt_ms, loss_rate)
    return _python_qft_decide(payload_size, rtt_ms, loss_rate)


def qft_decide_batch(payloads: list[tuple[int, float, float]]) -> list[tuple[int, int, float]]:
    if RUST_AVAILABLE:
        return _rust.qft_decide_batch(payloads)
    return [_python_qft_decide(s, r, l) for s, r, l in payloads]


def qft_decide_deadline_aware(
    payload_size: int,
    rtt_ms: float,
    loss_rate: float,
    deadline_ms: float,
    elapsed_ms: float,
) -> tuple:
    if RUST_AVAILABLE:
        return _rust.qft_decide_deadline_aware(payload_size, rtt_ms, loss_rate, deadline_ms, elapsed_ms)
    remaining_ms = deadline_ms - elapsed_ms
    is_emergency = remaining_ms < rtt_ms * 2.0 or remaining_ms < 5.0
    if is_emergency:
        return (4096, 0, True)
    chunk_size, strategy, _ = _python_qft_decide(payload_size, rtt_ms, loss_rate)
    return (chunk_size, strategy, False)


def qft_benchmark(n: int) -> float:
    if RUST_AVAILABLE:
        return _rust.qft_benchmark(n)
    import time
    t0 = time.monotonic()
    for i in range(n):
        _python_qft_decide(
            1024 * (1 + i % 1024),
            20.0 + (i % 100),
            0.01 * (i % 20),
        )
    elapsed = time.monotonic() - t0
    return n / elapsed if elapsed > 0 else 0.0


def _python_qft_decide(payload_size: int, rtt_ms: float, loss_rate: float) -> tuple:
    import math
    payload_norm = min(max(math.log10(max(payload_size, 1)), 0.0) / 8.0, 1.0)
    rtt_norm = min(rtt_ms / 500.0, 1.0)
    loss_norm = min(loss_rate / 0.2, 1.0)
    scores = [
        (1.0 - payload_norm) * 0.3 + loss_norm * 0.5 + (1.0 - rtt_norm) * 0.2,
        (1.0 - payload_norm)**2 * 0.4 + (1.0 - loss_norm) * loss_norm * 0.4 + 0.2,
        max(1.0 - abs(payload_norm - 0.5) * 2.0, 0.0) * 0.5 + (1.0 - loss_norm) * 0.3 + 0.2,
        payload_norm * 0.4 + (1.0 - loss_norm) * 0.4 + rtt_norm * 0.2,
        payload_norm**2 * 0.5 + (1.0 - loss_norm)**2 * 0.4 + (1.0 - rtt_norm) * 0.1,
    ]
    CHUNK_SIZES = [4096, 16384, 65536, 262144, 1048576]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    sorted_scores = sorted(scores, reverse=True)
    confidence = min((sorted_scores[0] - sorted_scores[1]) / sorted_scores[0], 1.0)
    return (CHUNK_SIZES[best_idx], best_idx, confidence)


# Chunker
def split_payload(payload: bytes, chunk_size: int) -> list[bytes]:
    if RUST_AVAILABLE:
        return _rust.split_payload(payload, chunk_size)
    if not payload or chunk_size <= 0: return []
    return [payload[i:i+chunk_size] for i in range(0, len(payload), chunk_size)]


def calculate_optimal_chunk_size(total_size: int, rtt_ms: float, bandwidth_mbps: float) -> int:
    if RUST_AVAILABLE:
        return _rust.calculate_optimal_chunk_size(total_size, rtt_ms, bandwidth_mbps)
    bdp_bytes = int(bandwidth_mbps * 1_000_000.0 / 8.0 * rtt_ms / 1000.0)
    bdp_chunk = max(bdp_bytes // 4, 4 * 1024)
    raw_chunk = min(bdp_chunk, total_size)
    
    def next_power_of_two_floor(n: int) -> int:
        if n == 0: return 4096
        power = 1
        while power * 2 <= n:
            power *= 2
        return power

    rounded = next_power_of_two_floor(raw_chunk)
    return min(max(rounded, 4 * 1024), 1024 * 1024)
