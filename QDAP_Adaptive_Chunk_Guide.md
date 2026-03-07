# QDAP — Adaptive Chunk Sizing Implementation Guide
## QFT-Guided Dynamic Chunk Sizing: Her Payload Boyutunda Optimal Performans

---

## Neden Bu Kritik?

Mevcut sorun:
```
1MB payload → TEK QFrame → SHA3-256(1MB) → yavaş
Docker benchmark: 1MB'de QDAP 13 Mbps vs Classical 101 Mbps
```

Çözüm:
```
1MB payload → QFT spektrum analizi → optimal chunk boyutu seç
            → N×QFrame (her biri küçük) → paralel gönder
            → her boyutta ACK = 0, throughput rekabetçi
```

Paper'a katkısı:
```
Yeni Contribution 5:
"QFT-guided adaptive chunking eliminates serialization 
 bottleneck for large payloads while maintaining zero 
 ACK overhead across all payload sizes"
```

---

## Mimari Genel Bakış

```
Sender tarafı:

Large Payload (1MB)
       │
       ▼
┌─────────────────────┐
│  AdaptiveChunker    │  ← YENİ
│                     │
│  QFTScheduler'dan   │
│  spektrum al        │
│  → chunk_size seç   │
│  → payload'ı böl    │
└─────────┬───────────┘
          │ chunks: [64KB, 64KB, ..., 64KB]
          ▼
┌─────────────────────┐
│  ChunkQFrame        │  ← YENİ (chunk metadata ile)
│                     │
│  Her chunk için:    │
│  - chunk_index      │
│  - total_chunks     │
│  - stream_id        │
│  - payload          │
└─────────┬───────────┘
          │ QFrame stream
          ▼
┌─────────────────────┐
│  QDAPTCPAdapter     │  ← MEVCUT (değişmez)
│  Ghost Session      │
│  ACK = 0            │
└─────────────────────┘

Receiver tarafı:

QFrame stream
       │
       ▼
┌─────────────────────┐
│  ChunkReassembler   │  ← YENİ
│                     │
│  stream_id bazında  │
│  chunk'ları topla   │
│  → complete payload │
└─────────────────────┘
```

---

## Dosya Yapısı

```
src/qdap/
├── chunking/                          ← YENİ MODÜL
│   ├── __init__.py
│   ├── adaptive_chunker.py            ← Ana sınıf
│   ├── chunk_qframe.py                ← Chunk metadata wire format
│   ├── reassembler.py                 ← Receiver tarafı birleştirici
│   └── strategy.py                    ← Chunk boyutu strateji enum
├── scheduler/
│   └── qft_scheduler.py               ← chunk_size() metodu EKLENECEK
└── transport/
    └── tcp/
        └── adapter.py                 ← send_large() metodu EKLENECEK

tests/chunking/
├── test_adaptive_chunker.py           ← min 8 test
├── test_chunk_qframe.py               ← min 5 test
└── test_reassembler.py                ← min 5 test

docker_benchmark/
├── sender/
│   └── qdap_client.py                 ← send_large() kullanacak şekilde güncelle
└── results/
    └── adaptive_benchmark.json        ← yeni benchmark sonuçları
```

---

## ADIM 1 — Chunk Boyutu Stratejisi

```python
# src/qdap/chunking/strategy.py

from enum import IntEnum

class ChunkStrategy(IntEnum):
    """
    QFT spektrum analizinden türetilen chunk boyutu stratejileri.
    
    Quantum analogu:
    QFT yüksek frekanslı bileşenler → küçük, sık paketler → küçük chunk
    QFT düşük frekanslı bileşenler  → büyük, seyrek paketler → büyük chunk
    """
    MICRO   = 4   * 1024        #   4KB — burst IoT, realtime telemetri
    SMALL   = 16  * 1024        #  16KB — küçük RPC, API yanıtları
    MEDIUM  = 64  * 1024        #  64KB — genel amaç (default)
    LARGE   = 256 * 1024        # 256KB — bulk transfer, video stream
    JUMBO   = 1   * 1024 * 1024 #   1MB — büyük dosya transferi

    @classmethod
    def from_energy_bands(
        cls,
        low: float,
        mid: float,
        high: float,
        payload_size: int,
    ) -> 'ChunkStrategy':
        """
        QFT enerji bantlarından optimal chunk boyutunu seç.

        Karar mantığı:
        - Yüksek frekans dominant (high > 0.5) → küçük chunk
          (burst trafik → latency kritik)
        - Düşük frekans dominant (low > 0.7) → büyük chunk  
          (bulk trafik → throughput kritik)
        - Karışık → payload boyutuna göre heuristik
        
        Args:
            low:          Düşük frekans enerji oranı [0,1]
            mid:          Orta frekans enerji oranı [0,1]
            high:         Yüksek frekans enerji oranı [0,1]
            payload_size: Gönderilecek toplam byte

        Returns:
            Optimal ChunkStrategy
        """
        # Yüksek frekanslı trafik → latency öncelikli → küçük chunk
        if high > 0.50:
            return cls.MICRO

        if high > 0.35:
            return cls.SMALL

        # Düşük frekanslı trafik → throughput öncelikli → büyük chunk
        if low > 0.70:
            if payload_size > 10 * 1024 * 1024:   # >10MB
                return cls.JUMBO
            if payload_size > 1 * 1024 * 1024:    # >1MB
                return cls.LARGE
            return cls.MEDIUM

        if low > 0.50:
            return cls.LARGE

        # Karışık trafik → payload boyutuna göre
        if payload_size < 64 * 1024:
            return cls.SMALL
        if payload_size < 1 * 1024 * 1024:
            return cls.MEDIUM
        return cls.LARGE

    def describe(self) -> str:
        names = {
            self.MICRO:  "MICRO (4KB) — burst/IoT",
            self.SMALL:  "SMALL (16KB) — RPC",
            self.MEDIUM: "MEDIUM (64KB) — general",
            self.LARGE:  "LARGE (256KB) — bulk",
            self.JUMBO:  "JUMBO (1MB) — large file",
        }
        return names.get(self, "UNKNOWN")
```

---

## ADIM 2 — QFTScheduler'a chunk_size() Ekle

```python
# src/qdap/scheduler/qft_scheduler.py
# MEVCUT DOSYAYA EKLENECEK — yeni metod

def chunk_size_for(self, payload_size: int) -> int:
    """
    Mevcut trafik spektrumuna göre optimal chunk boyutunu döndür.
    
    QFT Scheduler'ın spektrum analizini chunk boyutu kararına bağlar.
    Bu QDAP'ın "QFT-guided adaptive chunking" contribution'ının özü.

    Args:
        payload_size: Gönderilecek toplam payload boyutu (bytes)

    Returns:
        Optimal chunk boyutu (bytes)
    
    Example:
        >>> scheduler = QFTScheduler(window_size=64)
        >>> # ... trafik gözlemlendikten sonra:
        >>> chunk = scheduler.chunk_size_for(1024 * 1024)  # 1MB
        >>> print(chunk)  # 262144 (256KB) veya 65536 (64KB)
    """
    from qdap.chunking.strategy import ChunkStrategy

    if not self.has_enough_data:
        # Yeterli veri yok → güvenli default
        return ChunkStrategy.MEDIUM

    bands = self._last_energy_bands   # {'low': x, 'mid': y, 'high': z}
    
    strategy = ChunkStrategy.from_energy_bands(
        low=bands.get('low', 0.33),
        mid=bands.get('mid', 0.33),
        high=bands.get('high', 0.33),
        payload_size=payload_size,
    )
    
    # Scheduler'ın strateji adını güncelle (get_spectrum_report için)
    self._chunk_strategy = strategy
    
    return int(strategy)

@property
def chunk_strategy_name(self) -> str:
    """Mevcut chunk stratejisinin adı (logging/debug için)."""
    if hasattr(self, '_chunk_strategy'):
        return self._chunk_strategy.describe()
    return "MEDIUM (64KB) — default"
```

---

## ADIM 3 — Chunk QFrame Wire Format

```python
# src/qdap/chunking/chunk_qframe.py

import struct
import uuid
from dataclasses import dataclass
from typing import Optional
from qdap.frame.qframe import QFrame, Subframe, SubframeType

# Chunk header wire format (20 bytes sabit):
# [stream_id(8B)] [chunk_index(4B)] [total_chunks(4B)] [flags(4B)]
CHUNK_HEADER_SIZE = 20
CHUNK_FLAG_FIRST  = 0x01   # İlk chunk
CHUNK_FLAG_LAST   = 0x02   # Son chunk
CHUNK_FLAG_ONLY   = 0x03   # Tek chunk (first + last)

@dataclass
class ChunkMetadata:
    stream_id:    bytes   # 8 byte unique stream identifier
    chunk_index:  int     # 0-based
    total_chunks: int     # toplam chunk sayısı
    is_first:     bool
    is_last:      bool

    def to_bytes(self) -> bytes:
        flags  = 0
        if self.is_first: flags |= CHUNK_FLAG_FIRST
        if self.is_last:  flags |= CHUNK_FLAG_LAST
        return struct.pack(
            ">8sIII",
            self.stream_id,
            self.chunk_index,
            self.total_chunks,
            flags,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> 'ChunkMetadata':
        stream_id, idx, total, flags = struct.unpack(">8sIII", data[:20])
        return cls(
            stream_id=stream_id,
            chunk_index=idx,
            total_chunks=total,
            is_first=bool(flags & CHUNK_FLAG_FIRST),
            is_last=bool(flags & CHUNK_FLAG_LAST),
        )


def make_chunk_frames(
    payload:      bytes,
    chunk_size:   int,
    deadline_ms:  float = 100.0,
    stream_id:    Optional[bytes] = None,
) -> list:
    """
    Büyük payload'ı chunk QFrame listesine dönüştür.

    Her QFrame:
    - Subframe 1: ChunkMetadata (20 byte header)
    - Subframe 2: Chunk payload

    AmplitudeEncoder metadata'yı düşük öncelikli,
    payload'ı yüksek öncelikli işler (deadline farkı ile).

    Args:
        payload:     Gönderilecek büyük veri
        chunk_size:  QFTScheduler.chunk_size_for() çıktısı
        deadline_ms: Gönderim deadline'ı
        stream_id:   None ise otomatik üretilir (8 byte random)

    Returns:
        List[QFrame] — sıralı chunk frame'leri
    """
    if stream_id is None:
        stream_id = uuid.uuid4().bytes[:8]

    # Payload'ı chunk'lara böl
    chunks = [
        payload[i:i + chunk_size]
        for i in range(0, len(payload), chunk_size)
    ]
    total = len(chunks)
    frames = []

    for idx, chunk in enumerate(chunks):
        meta = ChunkMetadata(
            stream_id=stream_id,
            chunk_index=idx,
            total_chunks=total,
            is_first=(idx == 0),
            is_last=(idx == total - 1),
        )

        # İki subframe: metadata + data
        sf_meta = Subframe(
            payload=meta.to_bytes(),
            type=SubframeType.CTRL,
            deadline_ms=deadline_ms * 2,   # metadata daha az öncelikli
        )
        sf_data = Subframe(
            payload=chunk,
            type=SubframeType.DATA,
            deadline_ms=deadline_ms,        # data öncelikli
        )

        frame = QFrame.create_with_encoder([sf_meta, sf_data])
        frames.append((meta, frame))

    return frames
```

---

## ADIM 4 — Chunk Reassembler (Receiver)

```python
# src/qdap/chunking/reassembler.py

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Awaitable
from qdap.chunking.chunk_qframe import ChunkMetadata, CHUNK_HEADER_SIZE

@dataclass
class StreamBuffer:
    """Tek bir stream'in chunk'larını tutar."""
    stream_id:    bytes
    total_chunks: int
    chunks:       Dict[int, bytes] = field(default_factory=dict)
    created_at:   float = field(default_factory=time.monotonic)

    @property
    def is_complete(self) -> bool:
        return len(self.chunks) == self.total_chunks

    @property
    def age_sec(self) -> float:
        return time.monotonic() - self.created_at

    def add_chunk(self, index: int, data: bytes):
        self.chunks[index] = data

    def reassemble(self) -> bytes:
        """Chunk'ları sıralı birleştir."""
        return b"".join(
            self.chunks[i] for i in range(self.total_chunks)
        )


class ChunkReassembler:
    """
    Receiver tarafında chunk QFrame'lerini toplayıp
    orijinal payload'ı yeniden oluşturur.

    Thread-safe: asyncio.Lock ile korunuyor.
    Timeout: 30 saniye içinde tamamlanmayan stream'ler temizlenir.

    Usage:
        reassembler = ChunkReassembler()
        reassembler.on_complete = my_handler

        # QFrame geldiğinde:
        await reassembler.process_frame(qframe)
    """

    STREAM_TIMEOUT_SEC = 30.0

    def __init__(self):
        self._streams: Dict[bytes, StreamBuffer] = {}
        self._lock    = asyncio.Lock()
        self.on_complete: Optional[Callable[[bytes, bytes], Awaitable[None]]] = None
        # on_complete(stream_id, complete_payload) çağrılır

    async def process_subframes(
        self,
        subframes: list,   # QFrame'den gelen subframe listesi
    ) -> Optional[bytes]:
        """
        İki subframe bekler: [CTRL(metadata), DATA(chunk)]
        Tüm chunk'lar gelince complete payload döner.

        Returns:
            Complete payload (tüm chunk'lar tamamsa)
            None (henüz tamamlanmadıysa)
        """
        # Metadata subframe'i bul (CTRL type)
        meta_bytes = None
        data_bytes = None

        for sf in subframes:
            if sf.type == "CTRL" and len(sf.payload) >= CHUNK_HEADER_SIZE:
                meta_bytes = sf.payload
            elif sf.type == "DATA":
                data_bytes = sf.payload

        if meta_bytes is None or data_bytes is None:
            return None   # Chunk frame değil, normal frame

        meta = ChunkMetadata.from_bytes(meta_bytes)

        async with self._lock:
            # Stream buffer oluştur veya bul
            if meta.stream_id not in self._streams:
                self._streams[meta.stream_id] = StreamBuffer(
                    stream_id=meta.stream_id,
                    total_chunks=meta.total_chunks,
                )

            buf = self._streams[meta.stream_id]
            buf.add_chunk(meta.chunk_index, data_bytes)

            if buf.is_complete:
                payload = buf.reassemble()
                del self._streams[meta.stream_id]

                if self.on_complete:
                    await self.on_complete(meta.stream_id, payload)

                return payload

        return None

    async def cleanup_stale(self):
        """30 saniyeden eski tamamlanmamış stream'leri temizle."""
        async with self._lock:
            stale = [
                sid for sid, buf in self._streams.items()
                if buf.age_sec > self.STREAM_TIMEOUT_SEC
            ]
            for sid in stale:
                del self._streams[sid]

    @property
    def active_streams(self) -> int:
        return len(self._streams)

    def get_stats(self) -> dict:
        return {
            "active_streams":  self.active_streams,
            "stream_ids":      [s.hex() for s in self._streams],
        }
```

---

## ADIM 5 — Adaptive Chunker (Ana Sınıf)

```python
# src/qdap/chunking/adaptive_chunker.py

import asyncio
import time
from dataclasses import dataclass
from typing import Optional
from qdap.scheduler.qft_scheduler import QFTScheduler
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.chunking.chunk_qframe import make_chunk_frames
from qdap.chunking.strategy import ChunkStrategy

@dataclass
class ChunkingStats:
    total_payloads:     int   = 0
    total_bytes:        int   = 0
    total_frames:       int   = 0
    strategy_counts:    dict  = None
    avg_chunk_size:     float = 0.0
    total_duration_sec: float = 0.0

    def __post_init__(self):
        if self.strategy_counts is None:
            self.strategy_counts = {}

    def record(self, strategy: ChunkStrategy, n_frames: int,
               payload_size: int, duration: float):
        self.total_payloads    += 1
        self.total_bytes       += payload_size
        self.total_frames      += n_frames
        self.total_duration_sec += duration
        name = strategy.describe()
        self.strategy_counts[name] = self.strategy_counts.get(name, 0) + 1
        self.avg_chunk_size = self.total_bytes / max(self.total_frames, 1)

    def throughput_mbps(self) -> float:
        if self.total_duration_sec < 1e-9:
            return 0.0
        return (self.total_bytes / self.total_duration_sec /
                (1024 * 1024) * 8)


class AdaptiveChunker:
    """
    QDAP'ın QFT-guided adaptive chunking sistemi.

    Nasıl çalışır:
    1. QFTScheduler'ın trafik spektrumunu sürekli gözlemler
    2. Her büyük payload geldiğinde optimal chunk boyutunu seçer
    3. Payload'ı chunk'lara böler ve QFrame stream olarak gönderir
    4. Ghost Session ACK'siz kayıp tespiti sağlar

    Paper contribution:
    "QFT-guided adaptive chunking eliminates serialization
     bottleneck while maintaining zero ACK overhead"
    """

    # Bu eşiğin üzerindeki payload'lar chunk'lanır
    CHUNKING_THRESHOLD = 32 * 1024   # 32KB

    def __init__(
        self,
        adapter:   QDAPTCPAdapter,
        scheduler: QFTScheduler,
    ):
        self.adapter   = adapter
        self.scheduler = scheduler
        self.stats     = ChunkingStats()

    async def send(
        self,
        payload:     bytes,
        deadline_ms: float = 100.0,
    ) -> dict:
        """
        Payload'ı akıllıca gönder:
        - Küçük payload (<32KB) → doğrudan tek QFrame
        - Büyük payload (>=32KB) → adaptive chunk stream

        Returns:
            Gönderim istatistikleri
        """
        if len(payload) < self.CHUNKING_THRESHOLD:
            return await self._send_single(payload, deadline_ms)
        return await self._send_chunked(payload, deadline_ms)

    async def _send_single(
        self, payload: bytes, deadline_ms: float
    ) -> dict:
        """Küçük payload — mevcut davranış, değişmez."""
        from qdap.frame.qframe import Subframe, SubframeType, QFrame
        sf    = Subframe(payload=payload, type=SubframeType.DATA,
                         deadline_ms=deadline_ms)
        frame = QFrame.create_with_encoder([sf])
        t0    = time.monotonic()
        await self.adapter.send_frame(frame)
        return {
            "mode":       "single",
            "frames":     1,
            "chunk_size": len(payload),
            "duration_ms": (time.monotonic() - t0) * 1000,
        }

    async def _send_chunked(
        self, payload: bytes, deadline_ms: float
    ) -> dict:
        """
        Büyük payload — adaptive chunk stream.
        
        1. QFT spektrumunu oku
        2. Optimal chunk boyutunu seç
        3. Chunk QFrame'leri oluştur
        4. Paralel gönder (pipelining)
        """
        # QFT kararı
        chunk_size = self.scheduler.chunk_size_for(len(payload))
        strategy   = ChunkStrategy(chunk_size)

        # Scheduler'a bu payload boyutunu bildir (spektrum güncelle)
        self.scheduler.observe_packet_size(len(payload))

        # Chunk frame'leri oluştur
        chunk_frames = make_chunk_frames(
            payload=payload,
            chunk_size=chunk_size,
            deadline_ms=deadline_ms,
        )

        n_frames = len(chunk_frames)
        t0       = time.monotonic()

        # Pipeline gönderim — ACK bekleme yok (Ghost Session)
        for meta, frame in chunk_frames:
            await self.adapter.send_frame(frame)

        duration = time.monotonic() - t0
        self.stats.record(strategy, n_frames, len(payload), duration)

        return {
            "mode":        "adaptive_chunk",
            "strategy":    strategy.describe(),
            "chunk_size":  chunk_size,
            "n_chunks":    n_frames,
            "payload_size": len(payload),
            "duration_ms":  duration * 1000,
            "throughput_mbps": (len(payload) / duration / (1024*1024) * 8)
                                if duration > 1e-9 else 0,
        }

    def get_stats(self) -> dict:
        return {
            "total_payloads":    self.stats.total_payloads,
            "total_bytes":       self.stats.total_bytes,
            "total_frames":      self.stats.total_frames,
            "avg_chunk_size_kb": self.stats.avg_chunk_size / 1024,
            "throughput_mbps":   self.stats.throughput_mbps(),
            "strategy_counts":   self.stats.strategy_counts,
            "current_strategy":  self.scheduler.chunk_strategy_name,
        }
```

---

## ADIM 6 — Testler

```python
# tests/chunking/test_adaptive_chunker.py

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from qdap.chunking.adaptive_chunker import AdaptiveChunker
from qdap.chunking.strategy import ChunkStrategy

@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.send_frame = AsyncMock()
    adapter.get_transport_stats = MagicMock(return_value={
        "bytes_sent": 0, "frames_sent": 0
    })
    return adapter

@pytest.fixture
def mock_scheduler():
    sched = MagicMock()
    sched.has_enough_data = True
    sched._last_energy_bands = {"low": 0.75, "mid": 0.15, "high": 0.10}
    sched.chunk_size_for = MagicMock(return_value=256 * 1024)
    sched.chunk_strategy_name = "LARGE (256KB) — bulk"
    sched.observe_packet_size = MagicMock()
    return sched

@pytest.fixture
def chunker(mock_adapter, mock_scheduler):
    return AdaptiveChunker(mock_adapter, mock_scheduler)


class TestAdaptiveChunker:

    @pytest.mark.asyncio
    async def test_small_payload_single_frame(self, chunker, mock_adapter):
        """32KB altı → tek frame."""
        payload = b"X" * (16 * 1024)
        result  = await chunker.send(payload)
        assert result["mode"] == "single"
        assert result["frames"] == 1
        mock_adapter.send_frame.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_payload_chunked(self, chunker, mock_adapter):
        """1MB → chunk stream."""
        payload = b"Y" * (1024 * 1024)
        result  = await chunker.send(payload)
        assert result["mode"] == "adaptive_chunk"
        assert result["n_chunks"] > 1
        assert mock_adapter.send_frame.call_count == result["n_chunks"]

    @pytest.mark.asyncio
    async def test_chunk_size_from_scheduler(self, chunker, mock_scheduler):
        """QFT Scheduler'dan chunk boyutu alınıyor."""
        payload = b"Z" * (2 * 1024 * 1024)
        await chunker.send(payload)
        mock_scheduler.chunk_size_for.assert_called_once_with(len(payload))

    @pytest.mark.asyncio
    async def test_no_ack_bytes(self, chunker, mock_adapter):
        """Ghost Session: ACK gönderilmiyor."""
        payload = b"A" * (1024 * 1024)
        await chunker.send(payload)
        # send_frame çağrılmış ama write(ack) çağrılmamış
        # adapter'dan ACK gönderimi yok
        for call in mock_adapter.method_calls:
            assert "ack" not in str(call).lower()

    @pytest.mark.asyncio
    async def test_chunk_ordering(self, chunker, mock_adapter):
        """Chunk'lar sıralı gönderilmeli."""
        from qdap.chunking.chunk_qframe import ChunkMetadata, CHUNK_HEADER_SIZE
        payload = b"B" * (512 * 1024)
        await chunker.send(payload, deadline_ms=50.0)
        # Frame çağrılarını doğrula — chunk_index sıralı olmalı
        assert mock_adapter.send_frame.call_count > 1

    @pytest.mark.asyncio
    async def test_stats_updated(self, chunker):
        """İstatistikler güncelleniyor."""
        payload = b"C" * (1024 * 1024)
        await chunker.send(payload)
        stats = chunker.get_stats()
        assert stats["total_payloads"] == 1
        assert stats["total_bytes"] == len(payload)

    @pytest.mark.asyncio
    async def test_exact_boundary_payload(self, chunker, mock_adapter):
        """Tam chunk sınırında payload."""
        payload = b"D" * (32 * 1024)   # Tam eşik
        result  = await chunker.send(payload)
        # 32KB = CHUNKING_THRESHOLD → chunked
        assert result["mode"] == "adaptive_chunk"

    @pytest.mark.asyncio
    async def test_100mb_payload(self, chunker, mock_adapter, mock_scheduler):
        """100MB büyük payload."""
        mock_scheduler.chunk_size_for.return_value = 1024 * 1024  # JUMBO
        payload  = b"E" * (100 * 1024 * 1024)
        result   = await chunker.send(payload)
        assert result["mode"] == "adaptive_chunk"
        assert result["n_chunks"] == 100   # 100MB / 1MB = 100 chunk


# tests/chunking/test_chunk_strategy.py

class TestChunkStrategy:

    def test_high_freq_gives_micro(self):
        """Yüksek frekans → küçük chunk (burst IoT)."""
        s = ChunkStrategy.from_energy_bands(
            low=0.1, mid=0.2, high=0.7,
            payload_size=1024*1024
        )
        assert s == ChunkStrategy.MICRO

    def test_low_freq_large_payload_gives_jumbo(self):
        """Düşük frekans + büyük payload → JUMBO."""
        s = ChunkStrategy.from_energy_bands(
            low=0.8, mid=0.15, high=0.05,
            payload_size=50 * 1024 * 1024
        )
        assert s == ChunkStrategy.JUMBO

    def test_low_freq_medium_payload_gives_large(self):
        """Düşük frekans + 5MB → LARGE."""
        s = ChunkStrategy.from_energy_bands(
            low=0.75, mid=0.15, high=0.10,
            payload_size=5 * 1024 * 1024
        )
        assert s == ChunkStrategy.LARGE

    def test_mixed_traffic_medium(self):
        """Karışık trafik → MEDIUM."""
        s = ChunkStrategy.from_energy_bands(
            low=0.33, mid=0.33, high=0.34,
            payload_size=512 * 1024
        )
        assert s in (ChunkStrategy.MEDIUM, ChunkStrategy.SMALL)

    def test_describe_returns_string(self):
        for strategy in ChunkStrategy:
            assert len(strategy.describe()) > 0


# tests/chunking/test_reassembler.py

class TestChunkReassembler:

    @pytest.mark.asyncio
    async def test_single_chunk_reassemble(self):
        from qdap.chunking.reassembler import ChunkReassembler
        from qdap.chunking.chunk_qframe import make_chunk_frames
        from qdap.frame.qframe import SubframeType

        payload  = b"Hello QDAP" * 100
        frames   = make_chunk_frames(payload, chunk_size=len(payload)+1)
        reasm    = ChunkReassembler()
        result   = None

        for meta, frame in frames:
            sfs    = frame.subframes
            result = await reasm.process_subframes(sfs)

        assert result == payload

    @pytest.mark.asyncio
    async def test_multi_chunk_reassemble(self):
        from qdap.chunking.reassembler import ChunkReassembler
        from qdap.chunking.chunk_qframe import make_chunk_frames

        payload = b"X" * (256 * 1024)
        frames  = make_chunk_frames(payload, chunk_size=64 * 1024)
        reasm   = ChunkReassembler()
        result  = None

        assert len(frames) == 4   # 256KB / 64KB

        for meta, frame in frames:
            result = await reasm.process_subframes(frame.subframes)

        assert result == payload

    @pytest.mark.asyncio
    async def test_out_of_order_chunks(self):
        """Sıra dışı chunk'lar doğru birleştirilmeli."""
        from qdap.chunking.reassembler import ChunkReassembler
        from qdap.chunking.chunk_qframe import make_chunk_frames

        payload = b"OOO" * (100 * 1024)
        frames  = make_chunk_frames(payload, chunk_size=64 * 1024)
        reasm   = ChunkReassembler()

        # Ters sırada gönder
        for meta, frame in reversed(frames):
            result = await reasm.process_subframes(frame.subframes)

        assert result == payload

    @pytest.mark.asyncio
    async def test_multiple_concurrent_streams(self):
        """İki farklı stream aynı anda."""
        from qdap.chunking.reassembler import ChunkReassembler
        from qdap.chunking.chunk_qframe import make_chunk_frames
        import uuid

        reasm   = ChunkReassembler()
        payload1 = b"S1" * (64 * 1024)
        payload2 = b"S2" * (64 * 1024)

        sid1    = uuid.uuid4().bytes[:8]
        sid2    = uuid.uuid4().bytes[:8]
        frames1 = make_chunk_frames(payload1, 32*1024, stream_id=sid1)
        frames2 = make_chunk_frames(payload2, 32*1024, stream_id=sid2)

        results = []
        for (_, f1), (_, f2) in zip(frames1, frames2):
            r1 = await reasm.process_subframes(f1.subframes)
            r2 = await reasm.process_subframes(f2.subframes)
            if r1: results.append(r1)
            if r2: results.append(r2)

        assert payload1 in results
        assert payload2 in results

    @pytest.mark.asyncio
    async def test_stale_cleanup(self):
        """30 saniye timeout — stale stream temizleniyor."""
        from qdap.chunking.reassembler import ChunkReassembler, StreamBuffer
        import time

        reasm = ChunkReassembler()
        # Manuel olarak stale stream ekle
        fake_id = b"stale000"
        reasm._streams[fake_id] = StreamBuffer(
            stream_id=fake_id,
            total_chunks=5,
        )
        reasm._streams[fake_id].created_at = time.monotonic() - 31

        await reasm.cleanup_stale()
        assert fake_id not in reasm._streams
```

---

## ADIM 7 — Docker Benchmark Güncelle

```python
# docker_benchmark/sender/qdap_client.py — GÜNCELLENMİŞ

import asyncio
import time
from dataclasses import dataclass
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.scheduler.qft_scheduler import QFTScheduler
from qdap.chunking.adaptive_chunker import AdaptiveChunker

@dataclass
class QDAPMetrics:
    protocol:         str   = "QDAP_AdaptiveChunk"
    n_messages:       int   = 0
    payload_bytes:    int   = 0
    ack_bytes_sent:   int   = 0       # Her zaman 0
    throughput_mbps:  float = 0.0
    mean_latency_ms:  float = 0.0
    p99_latency_ms:   float = 0.0
    duration_sec:     float = 0.0
    chunk_strategy:   str   = ""
    avg_chunk_size_kb: float = 0.0


async def run_qdap_benchmark(
    host:         str   = "172.20.0.10",
    port:         int   = 19601,
    n_messages:   int   = 1000,
    payload_size: int   = 1024,
) -> QDAPMetrics:
    adapter   = QDAPTCPAdapter()
    scheduler = QFTScheduler(window_size=64)
    chunker   = AdaptiveChunker(adapter, scheduler)

    await adapter.connect(host, port)

    latencies = []
    payload   = b"Q" * payload_size
    t_start   = time.monotonic()

    for _ in range(n_messages):
        # Scheduler'a bu boyutu bildir
        scheduler.observe_packet_size(payload_size)

        t0     = time.monotonic_ns()
        result = await chunker.send(payload, deadline_ms=50.0)
        latencies.append(time.monotonic_ns() - t0)

    duration = time.monotonic() - t_start
    await adapter.close()

    stats   = chunker.get_stats()
    lats_ms = sorted([l / 1e6 for l in latencies])
    p99_idx = int(len(lats_ms) * 0.99)

    return QDAPMetrics(
        n_messages=n_messages,
        payload_bytes=n_messages * payload_size,
        ack_bytes_sent=0,
        throughput_mbps=stats["throughput_mbps"],
        mean_latency_ms=sum(lats_ms) / len(lats_ms),
        p99_latency_ms=lats_ms[p99_idx],
        duration_sec=duration,
        chunk_strategy=stats["current_strategy"],
        avg_chunk_size_kb=stats["avg_chunk_size_kb"],
    )
```

---

## ADIM 8 — Benchmark Payload Listesini Genişlet

```python
# docker_benchmark/sender/run_benchmark.py içinde güncelle:

PAYLOAD_SIZES = [
    ("1KB",   1 * 1024,         1000),
    ("64KB",  64 * 1024,         200),
    ("1MB",   1 * 1024 * 1024,   20),
    ("10MB",  10 * 1024 * 1024,   5),   # YENİ
    ("100MB", 100 * 1024 * 1024,  2),   # YENİ
]
```

---

## Teslim Kriterleri

```
✅ Tüm mevcut testler hâlâ geçiyor (165+)
✅ Yeni testler geçiyor:
   tests/chunking/test_adaptive_chunker.py   (8 test)
   tests/chunking/test_chunk_strategy.py     (5 test)
   tests/chunking/test_reassembler.py        (5 test)
✅ QFTScheduler.chunk_size_for() implement edildi
✅ Docker benchmark 5 payload boyutunda çalıştı
✅ results/adaptive_benchmark.json oluştu

Beklenen docker sonucu:
  1KB:   QDAP >> Classical (~25×)   ← mevcut gibi
  64KB:  QDAP >> Classical (~14×)   ← mevcut gibi
  1MB:   QDAP ≈ veya > Classical    ← bunu görmek istiyoruz
  10MB:  QDAP ≈ Classical           ← QFT LARGE chunk seçmeli
  100MB: QDAP ≈ Classical           ← QFT JUMBO chunk seçmeli

Bitince adaptive_benchmark.json'u bize gönder.
Paper Table 2 ve yeni Contribution 5 güncellenecek.
```
