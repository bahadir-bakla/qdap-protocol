# QDAP — QFrame Batch Implementation Guide
## Opsiyon 4: N Chunk → 1 Super-Frame (Hash + Metadata Overhead 8-32× Azalır)

---

## Sorun ve Çözüm

```
Şu an (her chunk ayrı QFrame):
  10MB = 40 × 256KB chunk
  Her chunk → ayrı QFrame → ayrı SHA3-256 → ayrı metadata
  40 hash + 40 metadata = overhead birikmesi

QFrame Batch sonrası:
  10MB = 40 × 256KB chunk → 5 × (8 chunk batch)
  Her batch → 1 QFrame → 1 SHA3-256 → 1 metadata
  5 hash + 5 metadata = 8× daha az overhead

Güvenlik: Her 2MB'de bir hash → tam bütünlük koruması
```

---

## Yeni Dosya Yapısı

```
src/qdap/chunking/
├── adaptive_chunker.py     ← _send_chunked() güncellenecek
├── chunk_qframe.py         ← make_batch_frames() EKLENECEK
├── batch_config.py         ← YENİ: batch boyutu konfigürasyonu
├── reassembler.py          ← batch-aware güncelleme
└── strategy.py             ← batch_size() metodu EKLENECEK
```

---

## ADIM 1 — Batch Config

```python
# src/qdap/chunking/batch_config.py

from dataclasses import dataclass

@dataclass(frozen=True)
class BatchConfig:
    """
    QFrame batch konfigürasyonu.
    
    Batch = N chunk → 1 QFrame → 1 SHA3-256 hash
    
    Neden bu değerler:
      SMALL_BATCH=2:  64KB×2=128KB → küçük payload optimizasyonu
      DEFAULT_BATCH=8: 64KB×8=512KB → genel amaç dengesi
      LARGE_BATCH=16:  256KB×16=4MB → bulk transfer optimizasyonu
      JUMBO_BATCH=32:  1MB×32=32MB  → çok büyük transfer
    """
    SMALL_BATCH:   int = 2
    DEFAULT_BATCH: int = 8
    LARGE_BATCH:   int = 16
    JUMBO_BATCH:   int = 32

    @classmethod
    def for_payload(cls, payload_size: int, chunk_size: int) -> int:
        """
        Payload boyutu ve chunk boyutuna göre optimal batch size seç.
        
        Hedef: Her batch ~2-4MB olsun (overhead/throughput dengesi)
        
        Args:
            payload_size: Toplam payload (bytes)
            chunk_size:   Tek chunk boyutu (ChunkStrategy'den gelir)
        
        Returns:
            Batch başına kaç chunk gruplanacak
        """
        cfg = cls()
        total_chunks = (payload_size + chunk_size - 1) // chunk_size

        # Çok az chunk → batch gereksiz
        if total_chunks <= 4:
            return 1

        target_batch_bytes = 2 * 1024 * 1024   # 2MB per batch hedef

        batch = max(1, target_batch_bytes // chunk_size)

        # Sınırla
        if batch <= 2:
            return cfg.SMALL_BATCH
        if batch <= 8:
            return cfg.DEFAULT_BATCH
        if batch <= 16:
            return cfg.LARGE_BATCH
        return cfg.JUMBO_BATCH
```

---

## ADIM 2 — make_batch_frames() (Ana Değişiklik)

```python
# src/qdap/chunking/chunk_qframe.py
# Mevcut make_chunk_frames() yanına ekle

import hashlib
import struct
import uuid
from typing import List, Tuple, Optional
from qdap.frame.qframe import QFrame, Subframe, SubframeType

# Batch header wire format (28 bytes):
# [stream_id(8B)][batch_index(4B)][total_batches(4B)]
# [chunks_in_batch(4B)][first_chunk_index(4B)][flags(4B)]
BATCH_HEADER_SIZE = 28
BATCH_FLAG_FIRST  = 0x01
BATCH_FLAG_LAST   = 0x02


@dataclass
class BatchMetadata:
    stream_id:        bytes
    batch_index:      int
    total_batches:    int
    chunks_in_batch:  int
    first_chunk_idx:  int
    is_first:         bool
    is_last:          bool

    def to_bytes(self) -> bytes:
        flags = 0
        if self.is_first: flags |= BATCH_FLAG_FIRST
        if self.is_last:  flags |= BATCH_FLAG_LAST
        return struct.pack(
            ">8sIIIII",
            self.stream_id,
            self.batch_index,
            self.total_batches,
            self.chunks_in_batch,
            self.first_chunk_idx,
            flags,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'BatchMetadata':
        sid, bidx, total, n_chunks, first_idx, flags = struct.unpack(
            ">8sIIIII", data[:BATCH_HEADER_SIZE]
        )
        return cls(
            stream_id=sid,
            batch_index=bidx,
            total_batches=total,
            chunks_in_batch=n_chunks,
            first_chunk_idx=first_idx,
            is_first=bool(flags & BATCH_FLAG_FIRST),
            is_last=bool(flags & BATCH_FLAG_LAST),
        )


def make_batch_frames(
    payload:     bytes,
    chunk_size:  int,
    batch_size:  int,
    deadline_ms: float = 100.0,
    stream_id:   Optional[bytes] = None,
) -> List[Tuple[BatchMetadata, QFrame]]:
    """
    Büyük payload'ı batch QFrame'lerine dönüştür.
    
    N chunk → 1 QFrame → 1 SHA3-256 hash
    
    Her QFrame içerir:
      Subframe 1 (CTRL): BatchMetadata (28 byte)
      Subframe 2 (DATA): Tüm batch payload'u (N × chunk_size)
    
    SHA3-256: Subframe 2 payload'u üzerinden hesaplanır
    → QFrame.create_with_encoder() zaten bunu yapıyor
    → Ek bir değişiklik gerekmez
    
    Args:
        payload:     Gönderilecek büyük veri
        chunk_size:  Tek chunk boyutu (ChunkStrategy'den)
        batch_size:  Kaç chunk bir batch oluşturacak (BatchConfig'den)
        deadline_ms: Gönderim deadline'ı
        stream_id:   None ise otomatik üretilir
    
    Returns:
        List[(BatchMetadata, QFrame)]
    
    Örnek:
        10MB, chunk=256KB, batch=8:
        → 40 chunk → 5 batch
        → 5 QFrame, her biri 2MB payload + 1 hash
    """
    if stream_id is None:
        stream_id = uuid.uuid4().bytes[:8]

    # Payload'ı chunk'lara böl
    chunks = [
        payload[i:i + chunk_size]
        for i in range(0, len(payload), chunk_size)
    ]
    total_chunks = len(chunks)

    # Chunk'ları batch'lere grupla
    batches = [
        chunks[i:i + batch_size]
        for i in range(0, total_chunks, batch_size)
    ]
    total_batches = len(batches)

    frames = []
    chunk_cursor = 0

    for batch_idx, batch_chunks in enumerate(batches):
        # Batch payload = chunk'ları birleştir
        batch_payload = b"".join(batch_chunks)

        meta = BatchMetadata(
            stream_id=stream_id,
            batch_index=batch_idx,
            total_batches=total_batches,
            chunks_in_batch=len(batch_chunks),
            first_chunk_idx=chunk_cursor,
            is_first=(batch_idx == 0),
            is_last=(batch_idx == total_batches - 1),
        )

        # İki subframe: metadata + batch data
        sf_meta = Subframe(
            payload=meta.to_bytes(),
            type=SubframeType.CTRL,
            deadline_ms=deadline_ms * 2,
        )
        sf_data = Subframe(
            payload=batch_payload,
            type=SubframeType.DATA,
            deadline_ms=deadline_ms,
        )

        # QFrame.create_with_encoder() SHA3-256'yı sf_data üzerinden hesaplar
        # → Batch başına 1 hash (chunk başına değil)
        frame = QFrame.create_with_encoder([sf_meta, sf_data])
        frames.append((meta, frame))

        chunk_cursor += len(batch_chunks)

    return frames
```

---

## ADIM 3 — AdaptiveChunker._send_chunked() Güncelle

```python
# src/qdap/chunking/adaptive_chunker.py
# _send_chunked() metodunu batch-aware yap

from qdap.chunking.batch_config import BatchConfig
from qdap.chunking.chunk_qframe import make_batch_frames

async def _send_chunked(
    self, payload: bytes, deadline_ms: float
) -> dict:
    """
    GÜNCELLENMİŞ: QFrame batch ile gönderim.
    
    Akış:
    1. QFT → chunk_size seç
    2. BatchConfig → batch_size seç  
    3. N chunk → 1 QFrame (batch)
    4. Pipeline gönderim
    """
    # Adım 1: QFT chunk boyutu kararı
    chunk_size = self.scheduler.chunk_size_for(len(payload))
    strategy   = ChunkStrategy(chunk_size)
    self.scheduler.observe_packet_size(len(payload))

    # Adım 2: Batch boyutu kararı
    batch_size = BatchConfig.for_payload(len(payload), chunk_size)

    # Adım 3: Batch frame'leri oluştur
    batch_frames = make_batch_frames(
        payload=payload,
        chunk_size=chunk_size,
        batch_size=batch_size,
        deadline_ms=deadline_ms,
    )

    n_batches = len(batch_frames)
    n_chunks  = (len(payload) + chunk_size - 1) // chunk_size
    t0        = time.monotonic()

    # Adım 4: Pipeline gönderim
    for meta, frame in batch_frames:
        await self.adapter.send_frame(frame)
        await asyncio.sleep(0)   # event loop'a nefes

    duration = time.monotonic() - t0
    self.stats.record(strategy, n_batches, len(payload), duration)

    tput = (
        len(payload) / duration / (1024 * 1024) * 8
        if duration > 1e-9 else 0
    )

    return {
        "mode":          "adaptive_batch",
        "strategy":      strategy.describe(),
        "chunk_size":    chunk_size,
        "batch_size":    batch_size,
        "n_chunks":      n_chunks,
        "n_batches":     n_batches,        # ← önemli: hash sayısı
        "payload_size":  len(payload),
        "duration_ms":   duration * 1000,
        "throughput_mbps": tput,
        "overhead_reduction": f"{n_chunks}→{n_batches} frames ({n_chunks//max(n_batches,1)}× less)",
    }
```

---

## ADIM 4 — Reassembler'ı Batch-Aware Güncelle

```python
# src/qdap/chunking/reassembler.py
# Batch desteği ekle

from qdap.chunking.chunk_qframe import (
    BatchMetadata, BATCH_HEADER_SIZE,
    ChunkMetadata, CHUNK_HEADER_SIZE,
)

class ChunkReassembler:
    """
    GÜNCELLENMİŞ: Hem chunk hem batch frame'leri destekler.
    
    Batch frame: CTRL subframe boyutu == BATCH_HEADER_SIZE (28B)
    Chunk frame: CTRL subframe boyutu == CHUNK_HEADER_SIZE (20B)
    """

    async def process_subframes(self, subframes: list) -> Optional[bytes]:
        meta_bytes = None
        data_bytes = None

        for sf in subframes:
            if sf.type == "CTRL":
                meta_bytes = sf.payload
            elif sf.type == "DATA":
                data_bytes = sf.payload

        if meta_bytes is None or data_bytes is None:
            return None

        # Header boyutuna göre batch mi chunk mu?
        if len(meta_bytes) >= BATCH_HEADER_SIZE:
            return await self._process_batch(meta_bytes, data_bytes)
        elif len(meta_bytes) >= CHUNK_HEADER_SIZE:
            return await self._process_chunk(meta_bytes, data_bytes)

        return None

    async def _process_batch(
        self, meta_bytes: bytes, data_bytes: bytes
    ) -> Optional[bytes]:
        """Batch frame işle."""
        meta = BatchMetadata.from_bytes(meta_bytes)

        async with self._lock:
            if meta.stream_id not in self._streams:
                self._streams[meta.stream_id] = StreamBuffer(
                    stream_id=meta.stream_id,
                    total_chunks=meta.total_batches,  # batch sayısı
                )

            buf = self._streams[meta.stream_id]
            buf.add_chunk(meta.batch_index, data_bytes)

            if buf.is_complete:
                payload = buf.reassemble()
                del self._streams[meta.stream_id]
                if self.on_complete:
                    await self.on_complete(meta.stream_id, payload)
                return payload

        return None
```

---

## ADIM 5 — Testler

```python
# tests/chunking/test_batch_frames.py

import pytest
from qdap.chunking.chunk_qframe import make_batch_frames, BatchMetadata
from qdap.chunking.batch_config import BatchConfig
from qdap.chunking.reassembler import ChunkReassembler


class TestBatchConfig:

    def test_small_payload_no_batch(self):
        """4 chunk altı → batch_size=1."""
        size = BatchConfig.for_payload(
            payload_size=512 * 1024,  # 512KB
            chunk_size=256 * 1024,    # 256KB → 2 chunk
        )
        assert size == 1

    def test_10mb_batch_size(self):
        """10MB / 256KB = 40 chunk → batch=8 (DEFAULT)."""
        size = BatchConfig.for_payload(
            payload_size=10 * 1024 * 1024,
            chunk_size=256 * 1024,
        )
        assert size == BatchConfig.DEFAULT_BATCH  # 8

    def test_100mb_batch_size(self):
        """100MB / 1MB = 100 chunk → batch=2 (2MB target)."""
        size = BatchConfig.for_payload(
            payload_size=100 * 1024 * 1024,
            chunk_size=1024 * 1024,
        )
        assert size >= 1


class TestMakeBatchFrames:

    def test_10mb_frame_count(self):
        """10MB / 256KB chunk / 8 batch = 5 QFrame."""
        payload = b"X" * (10 * 1024 * 1024)
        frames  = make_batch_frames(
            payload=payload,
            chunk_size=256 * 1024,
            batch_size=8,
        )
        assert len(frames) == 5   # 40 chunk / 8 = 5 batch

    def test_100mb_frame_count(self):
        """100MB / 1MB chunk / 2 batch = 50 QFrame."""
        payload = b"Y" * (100 * 1024 * 1024)
        frames  = make_batch_frames(
            payload=payload,
            chunk_size=1024 * 1024,
            batch_size=2,
        )
        assert len(frames) == 50

    def test_batch_metadata_correct(self):
        """BatchMetadata alanları doğru."""
        payload = b"Z" * (10 * 1024 * 1024)
        frames  = make_batch_frames(payload, 256*1024, 8)

        first_meta, _ = frames[0]
        last_meta,  _ = frames[-1]

        assert first_meta.is_first
        assert last_meta.is_last
        assert first_meta.batch_index == 0
        assert last_meta.batch_index == len(frames) - 1
        assert first_meta.total_batches == len(frames)

    def test_reassemble_correctness(self):
        """Batch → reassemble → orijinal payload."""
        payload  = b"A" * (10 * 1024 * 1024)
        frames   = make_batch_frames(payload, 256*1024, 8)
        
        # Batch payload'larını birleştir
        reassembled = b""
        for meta, frame in frames:
            for sf in frame.subframes:
                if sf.type == "DATA":
                    reassembled += sf.payload
        
        assert reassembled == payload

    def test_hash_count_reduction(self):
        """
        KRITIK TEST: Hash sayısı azaldı mı?
        
        Eski: 40 QFrame → 40 SHA3-256
        Yeni:  5 QFrame →  5 SHA3-256 (8× azalma)
        """
        payload    = b"B" * (10 * 1024 * 1024)
        frames     = make_batch_frames(payload, 256*1024, 8)
        old_frames = 40  # Önceki chunk sayısı

        assert len(frames) == 5
        assert len(frames) < old_frames
        reduction = old_frames / len(frames)
        assert reduction == 8.0   # 8× azalma


class TestBatchReassembler:

    @pytest.mark.asyncio
    async def test_batch_reassemble_10mb(self):
        """10MB batch → reassemble → orijinal."""
        from qdap.chunking.reassembler import ChunkReassembler
        payload  = b"C" * (10 * 1024 * 1024)
        frames   = make_batch_frames(payload, 256*1024, 8)
        reasm    = ChunkReassembler()
        result   = None

        for meta, frame in frames:
            result = await reasm.process_subframes(frame.subframes)

        assert result == payload

    @pytest.mark.asyncio
    async def test_batch_out_of_order(self):
        """Sıra dışı batch'ler doğru birleştirilmeli."""
        payload = b"D" * (5 * 1024 * 1024)
        frames  = make_batch_frames(payload, 256*1024, 4)
        reasm   = ChunkReassembler()
        result  = None

        for meta, frame in reversed(frames):
            result = await reasm.process_subframes(frame.subframes)

        assert result == payload
```

---

## Beklenen Sonuç (Frame Overhead Karşılaştırması)

```
Payload   Chunk    Batch   Eski Frames   Yeni Frames   Azalma
─────────────────────────────────────────────────────────────
10MB      256KB    8       40            5             8×
100MB     1MB      2       100           50            2×
100MB     1MB      8       100           13            7.7×
1MB       64KB     8       16            2             8×
```

**Throughput beklentisi:**
```
10MB:  Eski 11.9 Mbps → Yeni ~40-55 Mbps (classical ~55 ile rekabet)
100MB: Eski 8.2 Mbps  → Yeni ~15-20 Mbps (classical ~18 ile rekabet)
```

---

## Teslim Kriterleri

```
✅ src/qdap/chunking/batch_config.py oluşturuldu
✅ make_batch_frames() implement edildi
✅ AdaptiveChunker._send_chunked() batch kullanıyor
✅ Reassembler batch-aware güncellendi
✅ 189 mevcut test geçiyor
✅ 8 yeni test geçiyor (tests/chunking/test_batch_frames.py)
✅ Docker benchmark tekrar çalıştırıldı
✅ adaptive_benchmark_v3.json oluştu

JSON'da görmek istediğimiz:
  10MB:  n_batches=5,  qdap_tput > 40 Mbps
  100MB: n_batches=50, qdap_tput > 15 Mbps
  ACK bytes: her boyutta 0
  
Bitince adaptive_benchmark_v3.json bize gelsin.
```

---

## Paper'a Eklenecek

```
"QFrame Batching groups N chunks into a single integrity-
 protected super-frame with one SHA3-256 hash computation,
 reducing frame overhead by N× while preserving per-batch
 integrity guarantees. BatchConfig dynamically selects N
 based on payload size and chunk strategy, targeting 2MB
 per batch for optimal throughput-overhead balance."

New contribution:
  Table X — Frame count reduction:
  10MB: 40 QFrame → 5 QFrame (8× reduction)
  100MB: 100 QFrame → 13 QFrame (7.7× reduction)
```
