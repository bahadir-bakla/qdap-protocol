"""
Video/Audio/Subtitle Media Types
===================================

Data structures for simulated video streaming with QDAP.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from enum import IntEnum


class VideoQuality(IntEnum):
    LOW = 1     # 360p, ~500 Kbps
    MEDIUM = 2  # 720p, ~2.5 Mbps
    HIGH = 3    # 1080p, ~5 Mbps
    ULTRA = 4   # 4K, ~15 Mbps


FRAME_SIZES = {
    VideoQuality.LOW: 8_000,
    VideoQuality.MEDIUM: 40_000,
    VideoQuality.HIGH: 100_000,
    VideoQuality.ULTRA: 300_000,
}


@dataclass
class VideoFrame:
    frame_id: int
    quality: VideoQuality
    timestamp_ms: int
    is_keyframe: bool
    data: bytes
    deadline_ms: float = 16.0   # 60fps

    def serialize(self) -> bytes:
        flags = 0x01 if self.is_keyframe else 0x00
        header = struct.pack('>IHqB', self.frame_id, int(self.quality),
                             self.timestamp_ms, flags)
        return header + self.data

    @classmethod
    def generate(cls, frame_id: int, quality: VideoQuality) -> VideoFrame:
        import random
        size = FRAME_SIZES[quality]
        is_kf = (frame_id % 30 == 0)
        if is_kf:
            size = int(size * 2.5)
        data = bytes([frame_id % 256] * size)
        return cls(
            frame_id=frame_id, quality=quality,
            timestamp_ms=int(time.monotonic() * 1000),
            is_keyframe=is_kf, data=data,
        )


@dataclass
class AudioChunk:
    chunk_id: int
    timestamp_ms: int
    sample_rate: int
    data: bytes
    deadline_ms: float = 10.0   # Audio is priority!

    def serialize(self) -> bytes:
        header = struct.pack('>IqH', self.chunk_id, self.timestamp_ms, self.sample_rate)
        return header + self.data

    @classmethod
    def generate(cls, chunk_id: int) -> AudioChunk:
        data = bytes([chunk_id % 256] * 3072)  # ~3KB AAC
        return cls(
            chunk_id=chunk_id,
            timestamp_ms=int(time.monotonic() * 1000),
            sample_rate=48000, data=data,
        )


@dataclass
class Subtitle:
    sub_id: int
    text: str
    start_ms: int
    end_ms: int
    deadline_ms: float = 100.0   # Lowest priority

    def serialize(self) -> bytes:
        text_b = self.text.encode('utf-8')[:200]
        header = struct.pack('>IqI', self.sub_id, self.start_ms, len(text_b))
        return header + text_b
