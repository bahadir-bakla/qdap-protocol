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
    # Verify it's the real compiled Rust extension, not a namespace package
    # from the qdap_core/ source directory
    if hasattr(_rust, "hash_frame"):
        RUST_AVAILABLE = True
    else:
        _rust = None
        RUST_AVAILABLE = False
        log.debug("qdap_core imported but missing Rust symbols — using pure Python fallback")
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


def qft_get_weights() -> list[float]:
    """Current softmax weights [w_0..w_4] — Σ=1. Rust: thread-local theta, Python: uniform."""
    if RUST_AVAILABLE:
        return list(_rust.qft_get_weights())
    return [0.2] * 5  # uniform (no persistent state in pure-Python bridge)


def qft_reset_weights() -> None:
    """Reset Rust theta to uniform (no-op when Rust not available)."""
    if RUST_AVAILABLE:
        _rust.qft_reset_weights()


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


# FEC
def fec_encode(data: bytes, k: int, r: int) -> list[bytes]:
    """XOR systematic FEC encode. Returns k+r packets."""
    if RUST_AVAILABLE:
        return list(_rust.fec_encode(data, k, r))
    return _python_fec_encode(data, k, r)


def fec_decode(
    packets: list[bytes | None],
    k: int,
    r: int,
    original_len: int,
) -> bytes | None:
    """Recover original data from received FEC packets."""
    if RUST_AVAILABLE:
        return _rust.fec_decode(packets, k, r, original_len)
    return _python_fec_decode(packets, k, r, original_len)


def fec_effective_loss(p: float, k: int, r: int) -> float:
    """Exact binomial irrecoverable-loss probability after FEC."""
    if RUST_AVAILABLE:
        return _rust.fec_effective_loss(p, k, r)
    import math
    if r == 0:
        return max(0.0, min(1.0, p))
    n = k + r
    p = max(0.0, min(1.0, p))
    q = 1.0 - p
    return sum(
        math.comb(n, i) * (p ** i) * (q ** (n - i))
        for i in range(r + 1, n + 1)
    )


def fec_select_profile(
    loss_rate: float,
    is_emergency: bool,
    max_overhead: float = 3.0,
) -> tuple[str, int, int]:
    """Adaptive FEC profile selection. Returns (label, k, r)."""
    if RUST_AVAILABLE:
        return _rust.fec_select_profile(loss_rate, is_emergency, max_overhead)
    if is_emergency:
        return ("emergency", 1, 2) if max_overhead >= 3.0 else ("aggressive", 1, 1)
    if loss_rate >= 0.20:
        return ("balanced", 2, 2)
    if loss_rate >= 0.05:
        return ("reliable", 2, 1)
    return ("none", 1, 0)


def fec_ema_update(current: float, lost: int, sent: int, alpha: float) -> float:
    """EMA loss-rate update."""
    if RUST_AVAILABLE:
        return _rust.fec_ema_update(current, lost, sent, alpha)
    if sent == 0:
        return current
    return (1.0 - alpha) * current + alpha * (lost / sent)


def _python_fec_encode(data: bytes, k: int, r: int) -> list[bytes]:
    if k <= 0:
        raise ValueError("k must be >= 1")
    if r == 0:
        return [data]
    if k == 1:
        return [data] + [data for _ in range(r)]

    total = len(data)
    chunk_sz = (total + k - 1) // k
    chunks = []
    for i in range(k):
        start = i * chunk_sz
        end = min(start + chunk_sz, total)
        chunk = data[start:end].ljust(chunk_sz, b"\x00")
        chunks.append(chunk)

    parities = [bytearray(chunk_sz) for _ in range(r)]
    for i, chunk in enumerate(chunks):
        parity = parities[i % r]
        for pos, byte in enumerate(chunk):
            parity[pos] ^= byte
    return chunks + [bytes(p) for p in parities]


def _python_fec_decode(
    packets: list[bytes | None],
    k: int,
    r: int,
    original_len: int,
) -> bytes | None:
    if not packets:
        return None
    if r == 0:
        first = packets[0]
        return None if first is None else first[:original_len]
    if k == 1:
        for packet in packets:
            if packet is not None:
                return packet[:original_len]
        return None
    if packets.count(None) > r:
        return None

    data_packets = list(packets[:k])
    parity_packets = packets[k:]
    if all(packet is not None for packet in data_packets):
        return b"".join(packet for packet in data_packets if packet is not None)[:original_len]

    recovered = list(data_packets)
    for parity_idx, parity in enumerate(parity_packets):
        if parity is None:
            continue
        stripe = [i for i in range(k) if i % r == parity_idx]
        missing = [i for i in stripe if recovered[i] is None]
        if len(missing) == 1:
            acc = bytearray(parity)
            for idx in stripe:
                if idx == missing[0]:
                    continue
                known = recovered[idx]
                if known is None:
                    continue
                for pos, byte in enumerate(known):
                    if pos < len(acc):
                        acc[pos] ^= byte
            recovered[missing[0]] = bytes(acc)

    if any(packet is None for packet in recovered):
        return None
    return b"".join(packet for packet in recovered if packet is not None)[:original_len]


# Delta framing
def delta_wrap_full(payload: bytes) -> bytes:
    if RUST_AVAILABLE:
        return _rust.delta_wrap_full(payload)
    return bytes([0x00]) + payload


def delta_wrap_delta(bitmask: int, payload: bytes) -> bytes:
    if RUST_AVAILABLE:
        return _rust.delta_wrap_delta(bitmask, payload)
    return bytes([0x01]) + int(bitmask).to_bytes(2, "big") + payload


def delta_parse_header(frame: bytes) -> tuple[int, int]:
    if RUST_AVAILABLE:
        return _rust.delta_parse_header(frame)
    if not frame:
        raise ValueError("Empty delta frame")
    if frame[0] == 0x00:
        return (0x00, 0)
    if frame[0] == 0x01:
        if len(frame) < 3:
            raise ValueError(f"DELTA frame too short: {len(frame)} bytes")
        return (0x01, int.from_bytes(frame[1:3], "big"))
    raise ValueError(f"Unknown delta frame type: 0x{frame[0]:02X}")


def delta_get_payload(frame: bytes) -> bytes:
    if RUST_AVAILABLE:
        return _rust.delta_get_payload(frame)
    frame_type, _ = delta_parse_header(frame)
    return frame[1:] if frame_type == 0x00 else frame[3:]


def delta_compute_bitmask(field_order: list[str], changed_keys: list[str]) -> int:
    if RUST_AVAILABLE:
        return _rust.delta_compute_bitmask(field_order, changed_keys)
    changed = set(changed_keys)
    bitmask = 0
    for i, key in enumerate(field_order[:16]):
        if key in changed:
            bitmask |= 1 << i
    return bitmask


def delta_fields_from_bitmask(field_order: list[str], bitmask: int) -> list[str]:
    if RUST_AVAILABLE:
        return list(_rust.delta_fields_from_bitmask(field_order, bitmask))
    return [key for i, key in enumerate(field_order[:16]) if (bitmask >> i) & 1]


def delta_change_ratio(
    total_fields: int,
    changed_fields: int,
    threshold: float,
    max_fields: int,
) -> tuple[float, bool]:
    if RUST_AVAILABLE:
        return _rust.delta_change_ratio(total_fields, changed_fields, threshold, max_fields)
    ratio = changed_fields / total_fields if total_fields > 0 else 1.0
    return (ratio, ratio > threshold or total_fields > max_fields)


# Ghost Session
def ghost_sign(key: bytes, seq_num: int, payload: bytes) -> bytes:
    if RUST_AVAILABLE:
        return _rust.ghost_sign(key, seq_num, payload)
    import hashlib
    import hmac
    msg = int(seq_num).to_bytes(4, "big") + payload[:32]
    return hmac.new(key, msg, hashlib.sha256).digest()[:8]


def ghost_verify(key: bytes, seq_num: int, payload: bytes, sig: bytes) -> bool:
    if RUST_AVAILABLE:
        return bool(_rust.ghost_verify(key, seq_num, payload, sig))
    import hmac
    if len(sig) != 8:
        return False
    return hmac.compare_digest(ghost_sign(key, seq_num, payload), sig)


GhostWindow = _rust.GhostWindow if RUST_AVAILABLE and hasattr(_rust, "GhostWindow") else None


def _python_qft_decide(payload_size: int, rtt_ms: float, loss_rate: float) -> tuple:
    # Identical normalization to Rust channel_log_scores and Python qft_scheduler._channel_log_scores:
    # ln-normalization for payload + RTT (robust to outlier values), linear for loss.
    import math
    _LN_MAX_PAYLOAD = math.log(100 * 1024 * 1024)  # ln(100 MB)
    _LN_MAX_RTT     = math.log(501.0)               # ln(501 ms)
    _EPS            = 1e-9

    payload_norm = min(math.log(payload_size + 1) / _LN_MAX_PAYLOAD, 1.0)
    rtt_norm     = min(math.log(rtt_ms + 1)     / _LN_MAX_RTT,     1.0)
    loss_norm    = min(loss_rate / 0.2, 1.0)

    # log1p scores — same coefficients as Rust qft_scheduler.rs channel_log_scores
    scores = [
        # MICRO: small payload + high loss + high RTT → small chunks
        math.log1p((1-payload_norm)*0.35 + loss_norm*0.45 + rtt_norm*0.20),
        # SMALL: small-medium payload + moderate loss
        math.log1p((1-payload_norm)**2 * 0.40 + loss_norm*(1-loss_norm)*0.40 + 0.20),
        # MEDIUM: mid payload, normal conditions
        math.log1p(max(1 - abs(payload_norm-0.5)*2, 0)*0.50 + (1-loss_norm)*0.30 + 0.20),
        # LARGE: large payload + low loss + high RTT
        math.log1p(payload_norm*0.40 + (1-loss_norm)*0.40 + rtt_norm*0.20),
        # JUMBO: huge payload + zero loss + low RTT (LAN)
        math.log1p(payload_norm**2 * 0.50 + (1-loss_norm)**2 * 0.40 + (1-rtt_norm)*0.10),
    ]
    CHUNK_SIZES = [4096, 16384, 65536, 262144, 1048576]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    sorted_scores = sorted(scores, reverse=True)
    confidence = min((sorted_scores[0] - sorted_scores[1]) / (sorted_scores[0] + _EPS), 1.0)
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
