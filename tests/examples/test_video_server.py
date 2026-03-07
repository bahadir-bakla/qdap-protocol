"""
Video Stream Server Tests
============================
"""

import asyncio
import pytest

from examples.video.stream_server import QDAPVideoStreamServer
from examples.video.media_types import VideoFrame, AudioChunk, Subtitle, VideoQuality
from qdap.transport.loopback import LoopbackTransport


class TestMediaTypes:

    def test_video_frame_serialize(self):
        frame = VideoFrame.generate(0, VideoQuality.LOW)
        data = frame.serialize()
        assert len(data) > 8_000  # At least frame size
        assert frame.is_keyframe  # frame_id=0 → keyframe

    def test_audio_chunk_serialize(self):
        chunk = AudioChunk.generate(0)
        data = chunk.serialize()
        assert len(data) > 3072   # 3KB data + header

    def test_subtitle_serialize(self):
        sub = Subtitle(sub_id=0, text="Hello QDAP", start_ms=0, end_ms=2000)
        data = sub.serialize()
        assert b"Hello QDAP" in data

    def test_video_quality_levels(self):
        assert VideoQuality.LOW < VideoQuality.MEDIUM < VideoQuality.HIGH < VideoQuality.ULTRA


class TestVideoStreamServer:

    @pytest.mark.asyncio
    async def test_stream_produces_frames(self):
        server_t, client_t = LoopbackTransport.create_pair()
        server = QDAPVideoStreamServer()
        server.adapter = client_t

        stats = await server.stream(duration_sec=0.3)
        assert stats['frame_count'] > 0
        assert stats['quality_stability'] >= 0
        assert stats['quality_stability'] <= 1
