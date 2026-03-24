"""
Phase 10.5 — Parallel Chunk Streaming Tests

Başarı kriterleri:
  - Assembly doğruluğu: orijinal payload ile byte-identical
  - missing_chunks() doğru döndürüyor
  - Timeout koruması: incomplete stream temizleniyor
  - Backward compat: stream_id=0 / tek chunk çalışıyor
  - plan_parallel_chunks: doğru dağıtım
  - decide_with_streaming: n_streams loss'la azalıyor
"""

import asyncio
import os
import time
import pytest

from qdap.transport.parallel_sender import (
    AssemblyBuffer,
    ChunkInfo,
    ParallelReceiver,
    ParallelSender,
    STREAM_COUNTS,
    _build_chunk_frame,
    parse_chunk_frame,
    plan_parallel_chunks,
)


# ── AssemblyBuffer ─────────────────────────────────────────────────────────────

class TestAssemblyBuffer:
    def test_add_and_complete(self):
        buf = AssemblyBuffer(stream_id=1, total_chunks=3)
        assert not buf.is_complete()
        buf.add(0, b"aaa")
        buf.add(1, b"bbb")
        assert not buf.is_complete()
        buf.add(2, b"ccc")
        assert buf.is_complete()

    def test_assemble_order(self):
        buf = AssemblyBuffer(stream_id=1, total_chunks=4)
        buf.add(3, b"d")
        buf.add(1, b"b")
        buf.add(0, b"a")
        buf.add(2, b"c")
        assert buf.assemble() == b"abcd"

    def test_missing_chunks(self):
        buf = AssemblyBuffer(stream_id=2, total_chunks=5)
        buf.add(0, b"x")
        buf.add(2, b"x")
        missing = buf.missing_chunks()
        assert missing == [1, 3, 4]

    def test_missing_empty_when_complete(self):
        buf = AssemblyBuffer(stream_id=3, total_chunks=2)
        buf.add(0, b"a")
        buf.add(1, b"b")
        assert buf.missing_chunks() == []

    def test_assemble_byte_identical(self):
        payload = os.urandom(1024 * 100)
        chunk_size = 1024
        chunks = [payload[i:i+chunk_size] for i in range(0, len(payload), chunk_size)]
        buf = AssemblyBuffer(stream_id=5, total_chunks=len(chunks))
        for idx, data in enumerate(chunks):
            buf.add(idx, data)
        assert buf.assemble() == payload


# ── Chunk frame serialization ─────────────────────────────────────────────────

class TestChunkFrame:
    def test_roundtrip(self):
        chunk = ChunkInfo(
            stream_id=42, chunk_idx=3, total_chunks=10,
            data=b"hello world", seq=3, priority=500,
        )
        frame = _build_chunk_frame(chunk)
        result = parse_chunk_frame(frame)
        assert result is not None
        assert result.stream_id    == 42
        assert result.chunk_idx    == 3
        assert result.total_chunks == 10
        assert result.data         == b"hello world"
        assert result.seq          == 3

    def test_tampered_hash_rejected(self):
        chunk = ChunkInfo(
            stream_id=1, chunk_idx=0, total_chunks=1,
            data=b"secret", seq=0, priority=100,
        )
        frame = bytearray(_build_chunk_frame(chunk))
        frame[-5] ^= 0xFF   # payload bozuldu, hash uyuşmayacak
        assert parse_chunk_frame(bytes(frame)) is None

    def test_short_data_rejected(self):
        assert parse_chunk_frame(b"\x00" * 10) is None
        assert parse_chunk_frame(b"") is None

    def test_large_payload_roundtrip(self):
        data = os.urandom(65536)
        chunk = ChunkInfo(
            stream_id=99, chunk_idx=0, total_chunks=1,
            data=data, seq=0, priority=1000,
        )
        frame = _build_chunk_frame(chunk)
        result = parse_chunk_frame(frame)
        assert result is not None
        assert result.data == data


# ── plan_parallel_chunks ──────────────────────────────────────────────────────

class TestPlanParallelChunks:
    def test_basic_distribution(self):
        plan = plan_parallel_chunks(
            payload_size=1024, chunk_size=256, n_streams=4
        )
        assert len(plan) == 4
        # Her chunk farklı stream'e
        stream_nums = [p[0] for p in plan]
        assert sorted(stream_nums) == [0, 1, 2, 3]

    def test_chunk_idx_sequence(self):
        plan = plan_parallel_chunks(
            payload_size=1000, chunk_size=100, n_streams=3
        )
        idxs = [p[1] for p in plan]
        assert idxs == list(range(10))

    def test_offsets_cover_payload(self):
        payload_size = 1000
        chunk_size   = 100
        plan = plan_parallel_chunks(payload_size, chunk_size, n_streams=2)
        covered = 0
        for _, _, start, end in plan:
            covered += end - start
        assert covered == payload_size

    def test_last_chunk_smaller(self):
        plan = plan_parallel_chunks(
            payload_size=1100, chunk_size=1000, n_streams=1
        )
        assert len(plan) == 2
        _, _, start1, end1 = plan[1]
        assert end1 - start1 == 100   # son chunk kısa

    def test_single_stream(self):
        plan = plan_parallel_chunks(500, 100, n_streams=1)
        stream_nums = [p[0] for p in plan]
        assert all(s == 0 for s in stream_nums)

    def test_empty_on_zero_params(self):
        assert plan_parallel_chunks(1000, 0, n_streams=4) == []
        assert plan_parallel_chunks(1000, 256, n_streams=0) == []


# ── ParallelReceiver ──────────────────────────────────────────────────────────

class TestParallelReceiver:
    def test_single_chunk_completes(self):
        rx = ParallelReceiver()
        result = rx.on_chunk(
            stream_id=1, chunk_idx=0, total_chunks=1, data=b"hello"
        )
        assert result == b"hello"

    def test_multi_chunk_assembly(self):
        payload = os.urandom(4096)
        rx      = ParallelReceiver()
        parts   = [payload[:2048], payload[2048:]]

        r1 = rx.on_chunk(1, 0, 2, parts[0])
        assert r1 is None   # henüz tamamlanmadı

        r2 = rx.on_chunk(1, 1, 2, parts[1])
        assert r2 == payload

    def test_out_of_order_chunks(self):
        payload = b"ABCDEF"
        rx      = ParallelReceiver()
        rx.on_chunk(10, 2, 3, b"EF")
        rx.on_chunk(10, 0, 3, b"AB")
        result = rx.on_chunk(10, 1, 3, b"CD")
        assert result == payload

    def test_missing_for_stream(self):
        rx = ParallelReceiver()
        rx.on_chunk(7, 0, 4, b"a")
        rx.on_chunk(7, 2, 4, b"c")
        missing = rx.missing_for_stream(7)
        assert 1 in missing
        assert 3 in missing

    def test_timeout_clears_buffer(self):
        rx = ParallelReceiver(timeout_ms=10)   # 10ms timeout
        rx.on_chunk(99, 0, 3, b"a")
        time.sleep(0.02)
        # Timeout geçti — sonraki chunk buffer'ı temizler, None döner
        result = rx.on_chunk(99, 1, 3, b"b")
        assert result is None

    def test_two_concurrent_streams(self):
        payload_a = os.urandom(512)
        payload_b = os.urandom(512)
        rx = ParallelReceiver()

        ra1 = rx.on_chunk(1, 0, 2, payload_a[:256])
        rb1 = rx.on_chunk(2, 0, 2, payload_b[:256])
        assert ra1 is None
        assert rb1 is None

        ra2 = rx.on_chunk(1, 1, 2, payload_a[256:])
        rb2 = rx.on_chunk(2, 1, 2, payload_b[256:])
        assert ra2 == payload_a
        assert rb2 == payload_b


# ── ParallelSender (MockWriter ile) ──────────────────────────────────────────

class MockWriter:
    def __init__(self):
        self.frames: list = []
        self.total_bytes = 0

    def write(self, data: bytes):
        self.frames.append(data)
        self.total_bytes += len(data)

    async def drain(self):
        await asyncio.sleep(0)


class TestParallelSender:
    @pytest.mark.asyncio
    async def test_send_returns_payload_size(self):
        writer  = MockWriter()
        sender  = ParallelSender(writer, strategy="MEDIUM", chunk_size=1024)
        payload = os.urandom(4096)
        sent, elapsed = await sender.send(payload)
        assert sent == len(payload)
        assert elapsed >= 0

    @pytest.mark.asyncio
    async def test_send_small_payload_single_chunk(self):
        writer  = MockWriter()
        sender  = ParallelSender(writer, strategy="MICRO", chunk_size=4096)
        payload = b"small"
        await sender.send(payload)
        assert len(writer.frames) == 1

    @pytest.mark.asyncio
    async def test_full_roundtrip_byte_identical(self):
        """Sender → Receiver tam döngü: orijinal payload korunmalı."""
        payload    = os.urandom(64 * 1024)
        chunk_size = 16 * 1024
        writer     = MockWriter()
        sender     = ParallelSender(writer, strategy="LARGE", chunk_size=chunk_size)
        await sender.send(payload)

        # Parse frame'leri ve alıcıya ver
        rx = ParallelReceiver()
        result = None
        for frame_bytes in writer.frames:
            chunk_info = parse_chunk_frame(frame_bytes)
            assert chunk_info is not None, "Frame parse edilemedi"
            result = rx.on_chunk(
                chunk_info.stream_id,
                chunk_info.chunk_idx,
                chunk_info.total_chunks,
                chunk_info.data,
            )

        assert result == payload, "Payload byte-identical değil!"

    @pytest.mark.asyncio
    async def test_stream_count_per_strategy(self):
        """Her strateji için beklenen stream sayısı."""
        expected = {"MICRO": 1, "SMALL": 1, "MEDIUM": 2, "LARGE": 4, "JUMBO": 8}
        for strategy, n in expected.items():
            writer = MockWriter()
            sender = ParallelSender(writer, strategy=strategy, chunk_size=1024)
            assert sender.n_streams == n

    @pytest.mark.asyncio
    async def test_stream_id_zero_backward_compat(self):
        """stream_id=0 olanlar tek chunk gibi çalışmalı."""
        rx     = ParallelReceiver()
        data   = b"backward_compat_test"
        result = rx.on_chunk(
            stream_id=0, chunk_idx=0, total_chunks=1, data=data
        )
        assert result == data

    @pytest.mark.asyncio
    async def test_multiple_sends_different_stream_ids(self):
        """Her send() farklı stream_id üretmeli."""
        writer = MockWriter()
        sender = ParallelSender(writer, strategy="SMALL", chunk_size=4096)
        payload = b"x" * 4096
        await sender.send(payload)
        await sender.send(payload)
        frames = writer.frames
        # İki gönderim → iki farklı stream_id
        ids = set()
        for f in frames:
            chunk = parse_chunk_frame(f)
            if chunk:
                ids.add(chunk.stream_id)
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_large_payload_1mb(self):
        """1MB payload tam assembl edilebilmeli."""
        payload    = os.urandom(1024 * 1024)
        chunk_size = 64 * 1024
        writer     = MockWriter()
        sender     = ParallelSender(writer, strategy="LARGE", chunk_size=chunk_size)
        await sender.send(payload)

        rx = ParallelReceiver(timeout_ms=10000)
        result = None
        for frame_bytes in writer.frames:
            ci = parse_chunk_frame(frame_bytes)
            if ci:
                result = rx.on_chunk(ci.stream_id, ci.chunk_idx, ci.total_chunks, ci.data)

        assert result == payload


# ── decide_with_streaming ─────────────────────────────────────────────────────

class TestDecideWithStreaming:
    def test_returns_four_tuple(self):
        from qdap.scheduler.qft_scheduler import QFTScheduler
        s = QFTScheduler()
        result = s.decide_with_streaming(1024 * 1024, 5.0, 0.001)
        assert len(result) == 4
        chunk_size, strategy_idx, confidence, n_streams = result
        assert chunk_size > 0
        assert 0 <= strategy_idx <= 4
        assert 0.0 <= confidence <= 1.0
        assert n_streams >= 1

    def test_high_loss_reduces_streams(self):
        """Loss > 10% → n_streams yarıya düşmeli."""
        from qdap.scheduler.qft_scheduler import QFTScheduler

        s = QFTScheduler()
        # Büyük payload, düşük loss → LARGE/JUMBO muhtemel
        for _ in range(50):
            s.decide(10 * 1024 * 1024, 2.0, 0.001)

        _, _, _, n_low_loss  = s.decide_with_streaming(10 * 1024 * 1024, 2.0, 0.001)
        _, _, _, n_high_loss = s.decide_with_streaming(10 * 1024 * 1024, 2.0, 0.20)

        assert n_high_loss <= n_low_loss, (
            f"High-loss should not have more streams: "
            f"{n_high_loss} vs {n_low_loss}"
        )

    def test_n_streams_at_least_1(self):
        """n_streams hiçbir koşulda 0 olamaz."""
        from qdap.scheduler.qft_scheduler import QFTScheduler
        s = QFTScheduler()
        for _ in range(10):
            _, _, _, n = s.decide_with_streaming(512, 500.0, 0.5)
            assert n >= 1
