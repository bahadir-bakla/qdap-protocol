"""
QFrame — Quantum-Inspired Frame Structure
==========================================

Superposition prensibinden ilham alarak tek bir frame içinde
birden fazla payload'ı amplitude-weighted şekilde encode eder.

Frame Format (wire):
    ┌─────────────┬────────────┬──────────────────┬───────┐
    │ QDAP Version│ Frame Type │ Subframe Count   │ Flags │
    ├─────────────┴────────────┴──────────────────┴───────┤
    │              Session ID (64-bit)                     │
    ├──────────────────────────────────────────────────────┤
    │        Amplitude Vector (N × float32)                │
    ├──────────────────────────────────────────────────────┤
    │   Subframe #1: [length | type | seq_num | payload]   │
    │   Subframe #2: [length | type | seq_num | payload]   │
    │        ...                                           │
    ├──────────────────────────────────────────────────────┤
    │        QFrame Integrity Hash (SHA3-256)               │
    └──────────────────────────────────────────────────────┘

References:
    - arXiv:2311.10375 — Quantum Data Encoding: A Comparative Analysis
    - IETF RFC 9340 — Architectural Principles for a Quantum Internet
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

import numpy as np

from qdap._rust_bridge import qframe_serialize as _serialize
from qdap._rust_bridge import qframe_deserialize as _deserialize


class SubframeType(IntEnum):
    """QFrame subframe type codes — analogous to quantum basis states."""

    DATA = 0x01   # Veri subframe'leri taşır
    CTRL = 0x02   # Kontrol mesajları (handshake, teardown)
    GHOST = 0x03  # Ghost Session protocol mesajları
    PROBE = 0x04  # Kanal kalitesi ölçümü
    SYNC = 0x05   # Zaman senkronizasyonu

    @classmethod
    def priority_map(cls) -> dict[int, float]:
        """Default priority weights per subframe type."""
        return {
            cls.DATA: 1.0,
            cls.CTRL: 2.0,    # Control messages are higher priority
            cls.GHOST: 1.5,
            cls.PROBE: 0.5,
            cls.SYNC: 3.0,    # Sync is critical
        }


class FrameType(IntEnum):
    """Top-level QFrame type."""

    DATA = 0x01
    CTRL = 0x02
    GHOST = 0x03
    PROBE = 0x04
    SYNC = 0x05


# ─── Wire Format Constants ──────────────────────────────
QDAP_VERSION = 1
QDAP_MAGIC = b"\x51\x44\x41\x50"  # "QDAP"
HEADER_FORMAT = ">BBHH"  # version(1) + frame_type(1) + subframe_count(2) + flags(2)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
SESSION_ID_FORMAT = ">Q"  # uint64
SESSION_ID_SIZE = struct.calcsize(SESSION_ID_FORMAT)
INTEGRITY_HASH_SIZE = 32  # SHA3-256

# Subframe wire: [payload_length(4) | type(1) | seq_num(4) | payload(N)]
SUBFRAME_HEADER_FORMAT = ">IBI"  # length(4) + type(1) + seq_num(4) = 9 bytes
SUBFRAME_HEADER_SIZE = struct.calcsize(SUBFRAME_HEADER_FORMAT)


@dataclass
class Subframe:
    """
    A single payload unit within a QFrame.

    Each subframe carries one logical message with associated metadata
    used for amplitude-based priority calculation.
    """

    payload: bytes
    type: SubframeType = SubframeType.DATA
    deadline_ms: float = 1000.0
    seq_num: int = 0
    size_bytes: int = field(init=False)
    session_id: int = 0

    def __post_init__(self):
        self.size_bytes = len(self.payload)

    def serialize(self) -> bytes:
        """Serialize subframe to wire format: [length(4) | type(1) | seq_num(4) | payload(N)]."""
        return struct.pack(SUBFRAME_HEADER_FORMAT, self.size_bytes, self.type, self.seq_num) + self.payload

    @classmethod
    def deserialize(cls, data: bytes) -> tuple[Subframe, int]:
        """
        Deserialize a subframe from wire bytes.

        Returns:
            Tuple of (Subframe, bytes_consumed).
        """
        length, sf_type, seq_num = struct.unpack(SUBFRAME_HEADER_FORMAT, data[:SUBFRAME_HEADER_SIZE])
        payload = data[SUBFRAME_HEADER_SIZE : SUBFRAME_HEADER_SIZE + length]
        sf = cls(payload=payload, type=SubframeType(sf_type), seq_num=seq_num)
        return sf, SUBFRAME_HEADER_SIZE + length


@dataclass
class QFrame:
    """
    Quantum-Inspired Frame — the core transmission unit of QDAP.

    A QFrame carries N subframes with an amplitude vector that encodes
    priority weights following quantum state normalization: Σ|αᵢ|² = 1.
    """

    version: int = QDAP_VERSION
    frame_type: FrameType = FrameType.DATA
    session_id: int = 0
    flags: int = 0
    subframes: list[Subframe] = field(default_factory=list)
    amplitude_vector: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float32))

    @classmethod
    def create(
        cls,
        subframes: list[Subframe],
        session_id: int = 0,
        frame_type: FrameType = FrameType.DATA,
        amplitude_vector: Optional[np.ndarray] = None,
    ) -> QFrame:
        """
        Factory method to create a QFrame with subframes.

        If amplitude_vector is not provided, uniform weights are assigned
        and normalized so that Σ|αᵢ|² = 1.
        """
        if amplitude_vector is None:
            n = len(subframes)
            if n > 0:
                amplitude_vector = np.full(n, 1.0 / np.sqrt(n), dtype=np.float32)
            else:
                amplitude_vector = np.array([], dtype=np.float32)

        return cls(
            version=QDAP_VERSION,
            frame_type=frame_type,
            session_id=session_id,
            subframes=subframes,
            amplitude_vector=amplitude_vector,
        )

    @classmethod
    def create_with_encoder(
        cls,
        subframes: list[Subframe],
        session_id: int = 0,
        frame_type: FrameType = FrameType.DATA,
    ) -> QFrame:
        """
        Factory method that auto-applies AmplitudeEncoder to compute
        priority-based amplitude vector.

        This is the recommended way to create QFrames — it calculates
        optimal amplitudes from subframe metadata (deadline, size, type).
        """
        from qdap.frame.encoder import AmplitudeEncoder

        encoder = AmplitudeEncoder()
        amplitudes = encoder.encode(subframes)
        amplitude_f32 = amplitudes.astype(np.float32)

        return cls(
            version=QDAP_VERSION,
            frame_type=frame_type,
            session_id=session_id,
            subframes=subframes,
            amplitude_vector=amplitude_f32,
        )

    @property
    def subframe_count(self) -> int:
        return len(self.subframes)

    @property
    def send_order(self) -> list[int]:
        """
        Decode amplitude vector into transmission order.

        Highest |α|² → sent first (Born rule analogy).
        """
        if len(self.amplitude_vector) == 0:
            return []
        probabilities = self.amplitude_vector**2
        return np.argsort(probabilities)[::-1].tolist()

    def compute_integrity_hash(self) -> bytes:
        """Compute SHA3-256 integrity hash over the frame contents."""
        h = hashlib.sha3_256()
        h.update(struct.pack(HEADER_FORMAT, self.version, self.frame_type,
                             self.subframe_count, self.flags))
        h.update(struct.pack(SESSION_ID_FORMAT, self.session_id))
        h.update(self.amplitude_vector.tobytes())
        for sf in self.subframes:
            h.update(sf.serialize())
        return h.digest()

    def serialize(self) -> bytes:
        """
        Serialize QFrame to binary wire format.

        Layout:
            [Header(6B)] [SessionID(8B)] [Amplitudes(N×4B)]
            [Subframe₁] [Subframe₂] ... [SHA3-256(32B)]
        """
        parts = []

        # Header
        parts.append(struct.pack(
            HEADER_FORMAT,
            self.version,
            self.frame_type,
            self.subframe_count,
            self.flags,
        ))

        # Session ID
        parts.append(struct.pack(SESSION_ID_FORMAT, self.session_id))

        # Amplitude vector
        parts.append(self.amplitude_vector.astype(np.float32).tobytes())

        # Subframes
        for sf in self.subframes:
            parts.append(sf.serialize())

        # Integrity hash (over everything above)
        raw = b"".join(parts)
        integrity = hashlib.sha3_256(raw).digest()
        parts.append(integrity)

        return b"".join(parts)

    @classmethod
    def deserialize(cls, data: bytes) -> QFrame:
        """Deserialize a QFrame from binary wire format."""
        offset = 0

        # Header
        version, frame_type, subframe_count, flags = struct.unpack(
            HEADER_FORMAT, data[offset : offset + HEADER_SIZE]
        )
        offset += HEADER_SIZE

        # Session ID
        (session_id,) = struct.unpack(SESSION_ID_FORMAT, data[offset : offset + SESSION_ID_SIZE])
        offset += SESSION_ID_SIZE

        # Amplitude vector
        amp_size = subframe_count * 4  # float32 = 4 bytes
        amplitude_vector = np.frombuffer(data[offset : offset + amp_size], dtype=np.float32).copy()
        offset += amp_size

        # Subframes
        subframes = []
        for _ in range(subframe_count):
            sf, consumed = Subframe.deserialize(data[offset:])
            subframes.append(sf)
            offset += consumed

        # Integrity hash (last 32 bytes) — verify
        received_hash = data[offset : offset + INTEGRITY_HASH_SIZE]
        frame = cls(
            version=version,
            frame_type=FrameType(frame_type),
            session_id=session_id,
            flags=flags,
            subframes=subframes,
            amplitude_vector=amplitude_vector,
        )

        expected_hash = hashlib.sha3_256(data[:offset]).digest()
        if received_hash != expected_hash:
            raise ValueError("QFrame integrity check failed: hash mismatch")

        return frame

    def __repr__(self) -> str:
        return (
            f"QFrame(v={self.version}, type={self.frame_type.name}, "
            f"session={self.session_id:#x}, subframes={self.subframe_count}, "
            f"amplitudes={self.amplitude_vector})"
        )

    def to_bytes(self) -> bytes:
        return _serialize(
            payload=getattr(self, "payload", b""),
            priority=getattr(self, "priority", 0),
            deadline_ms=getattr(self, "deadline_ms", 500.0),
            sequence_number=getattr(self, "sequence_number", 0),
            frame_type=int(self.frame_type),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "QFrame":
        payload, priority, deadline_ms, seq_num, frame_type, hash_valid = \
            _deserialize(data)
        frame = cls.__new__(cls)
        frame.payload         = payload
        frame.priority        = priority
        frame.deadline_ms     = deadline_ms
        frame.sequence_number = seq_num
        frame.frame_type      = frame_type
        frame.hash_valid      = hash_valid
        return frame
