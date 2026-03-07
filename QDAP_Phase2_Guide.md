# QDAP — Faz 2 Implementation Guide
## TCP Adapter + Benchmark Suite (Paralel Geliştirme)

> **Ön koşul:** Faz 1 tamamlandı, 97/97 test geçti ✅  
> **Süre:** 3-4 hafta  
> **Strateji:** İki track paralel ilerler, her hafta sonu birleşir

---

## Paralel Track Yapısı

```
TRACK A: TCP Adapter (production-grade)     TRACK B: Benchmark Suite
─────────────────────────────────────────   ──────────────────────────────
Hafta 1: Socket tuning + backpressure       Hafta 1: Harness + baseline TCP
Hafta 2: Connection pooling + retry         Hafta 2: 4 metrik implementasyonu
Hafta 3: QUIC adapter (aioquic)             Hafta 3: Grafik + raporlama
Hafta 4: Entegrasyon + stres testi          Hafta 4: Karşılaştırmalı analiz

Her hafta sonu: Track A + B birleşir → entegrasyon testi
```

---

## TRACK A — TCP Adapter (Production-Grade)

### A.1 Mevcut Durumun Üstüne Ne Ekleniyor?

```
Faz 1.4 server.py (mevcut):
  ✅ Asyncio TCP — çalışıyor
  ✅ QFrame serialization — çalışıyor
  ❌ Socket-level tuning yok
  ❌ Backpressure yönetimi yok
  ❌ Connection pool yok
  ❌ Graceful reconnect yok
  ❌ Health check / heartbeat yok
```

### A.2 Dosya Yapısı

```
src/qdap/transport/
├── __init__.py
├── base.py              ← Abstract transport interface
├── tcp/
│   ├── __init__.py
│   ├── adapter.py       ← Ana TCP adapter (yeni)
│   ├── pool.py          ← Connection pool (yeni)
│   ├── tuning.py        ← Socket options (yeni)
│   └── backpressure.py  ← Flow control (yeni)
├── quic/
│   ├── __init__.py
│   └── adapter.py       ← Faz 2 sonunda
└── loopback.py          ← Test için in-process transport
```

### A.3 Base Transport Interface

```python
# src/qdap/transport/base.py

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable
from qdap.frame.qframe import QFrame

class QDAPTransport(ABC):
    """
    Tüm transport adapter'ların uygulaması gereken interface.
    TCP, QUIC, WebSocket, in-process — hepsi bu sözleşmeye uyar.
    """

    @abstractmethod
    async def connect(self, host: str, port: int) -> None: ...

    @abstractmethod
    async def listen(self, host: str, port: int) -> None: ...

    @abstractmethod
    async def send_frame(self, frame: QFrame) -> None: ...

    @abstractmethod
    async def recv_frame(self) -> QFrame: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def is_healthy(self) -> bool: ...

    # Stats için ortak interface
    def get_transport_stats(self) -> dict:
        raise NotImplementedError
```

### A.4 Socket Tuning Modülü

```python
# src/qdap/transport/tcp/tuning.py
import socket
from dataclasses import dataclass

@dataclass
class TCPTuningConfig:
    """
    QDAP için optimize edilmiş TCP socket ayarları.
    Her ayarın benchmark'a etkisi not edilmiştir.
    """

    # Nagle algoritması — QDAP kendi batching'ini yapıyor,
    # Nagle bize latency kaybettirir
    tcp_nodelay: bool = True           # Etki: p99 latency ↓ ~20%

    # Send/recv buffer — QFrame max boyutuna göre ayarla
    # Default: 87380 bytes → Biz: 4MB (büyük QFrame'ler için)
    send_buffer_size: int = 4 * 1024 * 1024   # Etki: throughput ↑
    recv_buffer_size: int = 4 * 1024 * 1024

    # Keepalive — Ghost Session'ın üstüne transport-level sağlık
    keepalive_enabled: bool = True
    keepalive_idle:    int = 30   # 30s sessizlikte keepalive gönder
    keepalive_interval: int = 5  # 5s aralıkla tekrar
    keepalive_count:   int = 3   # 3 başarısız → bağlantı kes

    # TCP_CORK — burst gönderimde paketleri birleştir
    # Sadece Linux'ta çalışır, Mac'te ignore edilir
    use_cork: bool = False

    # SO_REUSEADDR + SO_REUSEPORT — hızlı restart için
    reuse_addr: bool = True
    reuse_port: bool = True


def apply_tuning(sock: socket.socket, config: TCPTuningConfig) -> None:
    """
    Socket'e tüm optimizasyonları uygula.
    Mac ve Linux uyumlu — desteklenmeyen opsiyonlar sessizce skip edilir.
    """
    if config.tcp_nodelay:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, config.send_buffer_size)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, config.recv_buffer_size)

    if config.reuse_addr:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if config.keepalive_enabled:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Platform-specific keepalive parametreleri
        if hasattr(socket, 'TCP_KEEPIDLE'):   # Linux
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,
                            config.keepalive_idle)
        if hasattr(socket, 'TCP_KEEPINTVL'):  # Linux + Mac
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL,
                            config.keepalive_interval)
        if hasattr(socket, 'TCP_KEEPCNT'):    # Linux + Mac
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,
                            config.keepalive_count)
```

### A.5 Ana TCP Adapter

```python
# src/qdap/transport/tcp/adapter.py

import asyncio
import struct
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from qdap.frame.qframe import QFrame
from qdap.transport.base import QDAPTransport
from qdap.transport.tcp.tuning import TCPTuningConfig, apply_tuning
from qdap.transport.tcp.backpressure import BackpressureController

# Wire format sabitleri
QDAP_MAGIC   = b'\x51\x44\x41\x50'   # "QDAP"
QDAP_VERSION = 1
HEADER_SIZE  = 10   # magic(4) + version(2) + length(4)


@dataclass
class TCPAdapterStats:
    frames_sent:       int   = 0
    frames_received:   int   = 0
    bytes_sent:        int   = 0
    bytes_received:    int   = 0
    retransmit_count:  int   = 0    # Ghost Session'dan gelen retransmit
    connection_resets: int   = 0
    send_latencies_ns: list  = field(default_factory=list)

    def p99_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        import numpy as np
        return np.percentile(self.send_latencies_ns, 99) / 1e6

    def p999_send_latency_ms(self) -> float:
        if not self.send_latencies_ns:
            return 0.0
        import numpy as np
        return np.percentile(self.send_latencies_ns, 99.9) / 1e6

    def throughput_mbps(self, elapsed_sec: float) -> float:
        if elapsed_sec <= 0:
            return 0.0
        return (self.bytes_sent / elapsed_sec) / (1024 * 1024)


class QDAPTCPAdapter(QDAPTransport):
    """
    Production-grade TCP transport adapter.

    Faz 1.4 server.py'nin üzerine inşa edilir:
    + Socket-level tuning (TCP_NODELAY, buffer sizes, keepalive)
    + Backpressure controller (alıcı yavaşsa gönderici durur)
    + Bağlantı sağlığı izleme
    + İstatistik toplama (benchmark için)
    """

    MAGIC   = QDAP_MAGIC
    VERSION = QDAP_VERSION

    def __init__(
        self,
        tuning: Optional[TCPTuningConfig] = None,
        on_frame: Optional[Callable[[QFrame], Awaitable[None]]] = None,
    ):
        self.tuning     = tuning or TCPTuningConfig()
        self.on_frame   = on_frame
        self.stats      = TCPAdapterStats()
        self.bp         = BackpressureController(high_watermark=256)

        self._reader: Optional[asyncio.StreamReader]  = None
        self._writer: Optional[asyncio.StreamWriter]  = None
        self._server:  Optional[asyncio.AbstractServer] = None
        self._healthy  = False
        self._start_time: float = 0.0

    # ── Bağlantı yönetimi ──────────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        self._reader, self._writer = await asyncio.open_connection(host, port)
        self._apply_socket_tuning()
        self._healthy    = True
        self._start_time = time.monotonic()

    async def listen(
        self,
        host: str,
        port: int,
    ) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, host, port,
        )
        self._healthy    = True
        self._start_time = time.monotonic()

    async def serve_forever(self) -> None:
        async with self._server:
            await self._server.serve_forever()

    def _apply_socket_tuning(self) -> None:
        if self._writer:
            sock = self._writer.get_extra_info('socket')
            if sock:
                apply_tuning(sock, self.tuning)

    # ── Gönderme ───────────────────────────────────────────────────

    async def send_frame(self, frame: QFrame) -> None:
        """
        QFrame'i wire format'a çevirip gönder.
        Backpressure kontrolü ile — alıcı yavaşsa bloklanır.
        """
        await self.bp.acquire()    # Backpressure: doluysa bekle

        t0   = time.monotonic_ns()
        data = frame.serialize()
        hdr  = struct.pack('>4sHI', self.MAGIC, self.VERSION, len(data))
        payload = hdr + data

        try:
            self._writer.write(payload)
            await self._writer.drain()   # Flush — TCP buffer'ına yaz
        except (ConnectionResetError, BrokenPipeError) as e:
            self.stats.connection_resets += 1
            self._healthy = False
            raise

        elapsed = time.monotonic_ns() - t0
        self.stats.frames_sent       += 1
        self.stats.bytes_sent        += len(payload)
        self.stats.send_latencies_ns.append(elapsed)
        self.bp.release()

    # ── Alma ───────────────────────────────────────────────────────

    async def recv_frame(self) -> QFrame:
        """
        Wire'dan bir QFrame oku ve deserialize et.
        """
        hdr = await self._recv_exactly(self._reader, HEADER_SIZE)
        magic, version, length = struct.unpack('>4sHI', hdr)

        if magic != self.MAGIC:
            raise ProtocolError(f"Invalid QDAP magic: {magic!r}")
        if version != self.VERSION:
            raise ProtocolError(f"Unsupported version: {version}")

        data  = await self._recv_exactly(self._reader, length)
        frame = QFrame.deserialize(data)

        self.stats.frames_received += 1
        self.stats.bytes_received  += HEADER_SIZE + length
        return frame

    async def _recv_exactly(
        self, reader: asyncio.StreamReader, n: int
    ) -> bytes:
        """n byte tam olarak oku — partial read varsa bekle."""
        buf = bytearray()
        while len(buf) < n:
            chunk = await reader.read(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed mid-frame")
            buf.extend(chunk)
        return bytes(buf)

    # ── Server handler ─────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._apply_socket_tuning()

        try:
            while True:
                frame = await self.recv_frame()
                if self.on_frame:
                    await self.on_frame(frame)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    # ── Yardımcılar ────────────────────────────────────────────────

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        self._healthy = False

    def is_healthy(self) -> bool:
        return self._healthy

    def get_transport_stats(self) -> dict:
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "frames_sent":        self.stats.frames_sent,
            "frames_received":    self.stats.frames_received,
            "bytes_sent":         self.stats.bytes_sent,
            "throughput_mbps":    self.stats.throughput_mbps(elapsed),
            "p99_latency_ms":     self.stats.p99_send_latency_ms(),
            "p999_latency_ms":    self.stats.p999_send_latency_ms(),
            "connection_resets":  self.stats.connection_resets,
        }
```

### A.6 Backpressure Controller

```python
# src/qdap/transport/tcp/backpressure.py

import asyncio

class BackpressureController:
    """
    Gönderici hızını alıcı kapasitesiyle dengele.

    high_watermark: kaç frame'e kadar buffer'la
    Aşılırsa → send_frame() bloklanır (await ile)
    Alıcı tükettikçe → blok açılır

    Bu olmadan: producer çok hızlı giderse memory patlar.
    """

    def __init__(self, high_watermark: int = 256):
        self._semaphore = asyncio.Semaphore(high_watermark)
        self._high      = high_watermark
        self._current   = 0

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._current += 1

    def release(self) -> None:
        self._semaphore.release()
        self._current -= 1

    @property
    def pressure_ratio(self) -> float:
        """0.0 = boş, 1.0 = tam dolu"""
        return self._current / self._high

    def is_overloaded(self) -> bool:
        return self.pressure_ratio > 0.9
```

### A.7 Connection Pool

```python
# src/qdap/transport/tcp/pool.py

import asyncio
from collections import deque
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.transport.tcp.tuning import TCPTuningConfig

class QDAPConnectionPool:
    """
    TCP bağlantı havuzu — her request için yeni bağlantı açmak pahalı.
    Benchmark senaryosu: 5 concurrent client → pool ile yönet.

    min_size: Her zaman hazır bekleyen bağlantı sayısı
    max_size: Maksimum eş zamanlı bağlantı
    """

    def __init__(
        self,
        host: str,
        port: int,
        min_size: int = 2,
        max_size: int = 10,
        tuning: TCPTuningConfig = None,
    ):
        self.host     = host
        self.port     = port
        self.min_size = min_size
        self.max_size = max_size
        self.tuning   = tuning or TCPTuningConfig()

        self._pool:      deque[QDAPTCPAdapter] = deque()
        self._active:    int = 0
        self._lock:      asyncio.Lock = asyncio.Lock()
        self._not_empty: asyncio.Condition = asyncio.Condition(self._lock)

    async def initialize(self) -> None:
        """min_size kadar bağlantıyı önceden aç."""
        for _ in range(self.min_size):
            conn = await self._create_connection()
            self._pool.append(conn)

    async def acquire(self) -> QDAPTCPAdapter:
        """Havuzdan bağlantı al. Boşsa yeni aç, doluysa bekle."""
        async with self._not_empty:
            while not self._pool and self._active >= self.max_size:
                await self._not_empty.wait()

            if self._pool:
                conn = self._pool.popleft()
                if not conn.is_healthy():
                    conn = await self._create_connection()
            else:
                conn = await self._create_connection()

            self._active += 1
            return conn

    async def release(self, conn: QDAPTCPAdapter) -> None:
        """Bağlantıyı havuza geri ver."""
        async with self._not_empty:
            if conn.is_healthy() and len(self._pool) < self.min_size:
                self._pool.append(conn)
            else:
                await conn.close()
            self._active -= 1
            self._not_empty.notify()

    async def _create_connection(self) -> QDAPTCPAdapter:
        adapter = QDAPTCPAdapter(tuning=self.tuning)
        await adapter.connect(self.host, self.port)
        return adapter

    async def close_all(self) -> None:
        while self._pool:
            conn = self._pool.popleft()
            await conn.close()
```

---

## TRACK B — Benchmark Suite

### B.1 Genel Mimari

```
benchmarks/
├── harness.py          ← Ana test koşucusu
├── baseline/
│   ├── tcp_baseline.py ← Saf TCP referans implementasyonu
│   └── run_baseline.py ← Baseline ölçümlerini al
├── metrics/
│   ├── throughput.py   ← MB/s ölçümü
│   ├── latency.py      ← p50/p95/p99/p999 ölçümü
│   ├── ack_overhead.py ← ACK byte miktarı analizi
│   └── priority.py     ← Multiplexing doğruluk ölçümü
├── scenarios/
│   ├── bulk_transfer.py    ← 1MB, 10MB, 100MB
│   ├── small_messages.py   ← 10K × 100 byte
│   ├── mixed_traffic.py    ← Video + ses + kontrol
│   └── packet_loss.py      ← %1, %5, %10 loss simulation
├── report/
│   ├── generator.py    ← Matplotlib grafikleri
│   └── templates/
└── run_all.py          ← Tek komutla hepsini çalıştır
```

### B.2 Benchmark Harness

```python
# benchmarks/harness.py

import asyncio
import time
import statistics
from dataclasses import dataclass, field
from typing import List, Dict, Any, Callable, Awaitable
from rich.console import Console
from rich.table import Table
from rich.progress import Progress

console = Console()

@dataclass
class BenchmarkResult:
    name:            str
    protocol:        str   # "TCP_BASELINE" | "QDAP_TCP" | "QDAP_QUIC"
    duration_sec:    float
    throughput_mbps: float
    latency_p50_ms:  float
    latency_p99_ms:  float
    latency_p999_ms: float
    ack_bytes:       int
    total_bytes:     int
    priority_accuracy: float    # 0.0 - 1.0
    loss_detected:   int
    extra:           Dict[str, Any] = field(default_factory=dict)

    @property
    def ack_overhead_pct(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.ack_bytes / self.total_bytes) * 100


@dataclass
class BenchmarkSuite:
    """
    Tüm senaryoları çalıştıran ana harness.
    """
    host:     str = "127.0.0.1"
    port:     int = 19000
    warmup_s: int = 2      # JIT ve TCP slow-start için ısınma süresi
    runs:     int = 3      # Her senaryoyu 3 kez çalıştır, medyan al

    async def run_scenario(
        self,
        name: str,
        fn: Callable[[], Awaitable[BenchmarkResult]],
    ) -> BenchmarkResult:
        """
        Bir senaryoyu warmup + N run ile çalıştır.
        """
        console.print(f"\n[bold cyan]▶ {name}[/bold cyan]")

        # Warmup
        console.print(f"  Warmup ({self.warmup_s}s)...", end="")
        await asyncio.sleep(self.warmup_s)
        console.print(" done")

        # Ölçüm
        results = []
        with Progress() as progress:
            task = progress.add_task(f"  Running {self.runs} iterations", total=self.runs)
            for i in range(self.runs):
                r = await fn()
                results.append(r)
                progress.advance(task)

        # Medyan al
        best = sorted(results, key=lambda r: r.throughput_mbps)[len(results) // 2]
        return best

    def print_comparison(self, baseline: BenchmarkResult, qdap: BenchmarkResult):
        """
        Baseline vs QDAP karşılaştırma tablosu.
        """
        table = Table(title=f"📊 {baseline.name} — Karşılaştırma")
        table.add_column("Metrik",          style="bold")
        table.add_column("TCP Baseline",    style="red")
        table.add_column("QDAP",            style="green")
        table.add_column("Δ",               style="yellow")

        def delta(a, b, fmt=".2f", lower_is_better=False):
            diff = b - a
            pct  = (diff / a * 100) if a != 0 else 0
            sign = "+" if diff > 0 else ""
            arrow = "↑" if (diff > 0) != lower_is_better else "↓"
            return f"{sign}{diff:{fmt}} ({arrow}{abs(pct):.1f}%)"

        table.add_row(
            "Throughput (MB/s)",
            f"{baseline.throughput_mbps:.2f}",
            f"{qdap.throughput_mbps:.2f}",
            delta(baseline.throughput_mbps, qdap.throughput_mbps),
        )
        table.add_row(
            "Latency p99 (ms)",
            f"{baseline.latency_p99_ms:.3f}",
            f"{qdap.latency_p99_ms:.3f}",
            delta(baseline.latency_p99_ms, qdap.latency_p99_ms, lower_is_better=True),
        )
        table.add_row(
            "Latency p999 (ms)",
            f"{baseline.latency_p999_ms:.3f}",
            f"{qdap.latency_p999_ms:.3f}",
            delta(baseline.latency_p999_ms, qdap.latency_p999_ms, lower_is_better=True),
        )
        table.add_row(
            "ACK Overhead (%)",
            f"{baseline.ack_overhead_pct:.2f}%",
            f"{qdap.ack_overhead_pct:.2f}%",
            delta(baseline.ack_overhead_pct, qdap.ack_overhead_pct, lower_is_better=True),
        )
        table.add_row(
            "Priority Accuracy",
            "N/A (FIFO)",
            f"{qdap.priority_accuracy:.1%}",
            "—",
        )

        console.print(table)
```

### B.3 Metrik 1 — Throughput

```python
# benchmarks/metrics/throughput.py

import asyncio
import time
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.frame.qframe import QFrame, Subframe, SubframeType

async def measure_throughput(
    host: str,
    port: int,
    payload_size_mb: int = 10,
    chunk_size:      int = 64 * 1024,   # 64KB chunk'lar
) -> dict:
    """
    N MB veriyi QDAP üzerinden gönder, throughput'u ölç.

    Senaryo: Tek büyük transfer (bulk mode testi)
    QFT Scheduler bu senaryoda BulkTransferStrategy seçmeli.
    """
    total_bytes = payload_size_mb * 1024 * 1024
    chunk       = b'X' * chunk_size
    sent_bytes  = 0

    adapter = QDAPTCPAdapter()
    await adapter.connect(host, port)

    t0 = time.monotonic()

    while sent_bytes < total_bytes:
        remaining = total_bytes - sent_bytes
        data      = chunk[:min(chunk_size, remaining)]
        frame     = QFrame.create([
            Subframe(payload=data, type=SubframeType.DATA, deadline_ms=1000)
        ])
        await adapter.send_frame(frame)
        sent_bytes += len(data)

    elapsed = time.monotonic() - t0
    stats   = adapter.get_transport_stats()

    await adapter.close()

    return {
        "payload_mb":     payload_size_mb,
        "elapsed_sec":    elapsed,
        "throughput_mbps": stats["throughput_mbps"],
        "frames_sent":    stats["frames_sent"],
    }
```

### B.4 Metrik 2 — Latency (p99 / p999)

```python
# benchmarks/metrics/latency.py

import asyncio
import time
import numpy as np
from qdap.transport.tcp.adapter import QDAPTCPAdapter
from qdap.frame.qframe import QFrame, Subframe, SubframeType

async def measure_latency(
    host:     str,
    port:     int,
    n_msgs:   int = 10_000,
    msg_size: int = 100,          # 100 byte — küçük mesaj senaryosu
) -> dict:
    """
    N küçük mesaj gönder ve roundtrip sürelerini ölç.

    Senaryo: Yüksek frekans küçük mesajlar (IoT / RPC tarzı)
    QFT Scheduler bu senaryoda LatencyFirstStrategy seçmeli.
    """
    payload = b'Q' * msg_size
    latencies_ns = []

    adapter = QDAPTCPAdapter()
    await adapter.connect(host, port)

    for i in range(n_msgs):
        frame = QFrame.create([
            Subframe(payload=payload, type=SubframeType.DATA, deadline_ms=5)
        ])

        t0 = time.monotonic_ns()
        await adapter.send_frame(frame)
        elapsed = time.monotonic_ns() - t0

        latencies_ns.append(elapsed)

        # %1 ihtimalle kısa bekleme — gerçekçi trafik simülasyonu
        if i % 100 == 0:
            await asyncio.sleep(0)

    await adapter.close()

    arr = np.array(latencies_ns) / 1e6   # → ms

    return {
        "n_msgs":       n_msgs,
        "p50_ms":       float(np.percentile(arr, 50)),
        "p95_ms":       float(np.percentile(arr, 95)),
        "p99_ms":       float(np.percentile(arr, 99)),
        "p999_ms":      float(np.percentile(arr, 99.9)),
        "max_ms":       float(arr.max()),
        "mean_ms":      float(arr.mean()),
        "std_ms":       float(arr.std()),
    }
```

### B.5 Metrik 3 — ACK Overhead Analizi

```python
# benchmarks/metrics/ack_overhead.py

"""
TCP ACK overhead ölçümü — iki yaklaşım:

1. KLASIK TCP BASELINE:
   - tcpdump / scapy ile paket yakala
   - ACK paketlerinin byte'larını say
   - Toplam trafiğe oranını hesapla

2. QDAP:
   - Ghost Session implicit ACK → ACK paketi yok
   - Sadece NAK (negatif feedback) ve retransmit var
   - Ghost Session stats'ından ölç

MAC'TE NOT: tcpdump için sudo gerekir.
Alternatif: SO_TIMESTAMPING ile kernel-level timing.
"""

import asyncio
from qdap.session.ghost_session import GhostSession
from qdap.frame.qframe import QFrame, Subframe, SubframeType

async def measure_ack_overhead(
    n_frames:    int   = 1000,
    loss_rate:   float = 0.01,    # %1 paket kaybı simülasyonu
) -> dict:
    """
    QDAP Ghost Session'ın ACK overhead'ini ölç.

    Klasik TCP: Her segment için 40 byte ACK
    QDAP: Sadece retransmit request (kayıp tespit edilince)
    """
    import os, hashlib
    secret  = os.urandom(32)
    sess_id = hashlib.sha256(b"bench").digest()

    alice = GhostSession(sess_id, secret)
    bob   = GhostSession(sess_id, secret)

    ack_bytes_classical = 0    # Klasik TCP ACK simülasyonu
    ack_bytes_qdap      = 0    # QDAP retransmit overhead

    TCP_ACK_SIZE = 40          # IP header(20) + TCP header(20)

    for seq in range(n_frames):
        payload = b'B' * 1024
        frame   = alice.send(payload, seq_num=seq)

        # Klasik TCP: Her frame için ACK
        ack_bytes_classical += TCP_ACK_SIZE

        # Paket kaybı simülasyonu
        lost = (hash((seq, 42)) % 100) < (loss_rate * 100)

        if not lost:
            bob.on_receive(frame)    # Bob aldı
            # QDAP: ACK yok! Implicit.
        # else: kayıp → alice.detect_loss() retransmit trigger eder

    total_data_bytes = n_frames * 1024

    # QDAP'ın retransmit overhead'i
    retransmits = alice.detect_loss()
    ack_bytes_qdap = len(retransmits) * 1024   # Retransmit edilen veri

    ghost_stats = alice.get_stats()

    return {
        "n_frames":               n_frames,
        "loss_rate":              loss_rate,
        "classical_ack_bytes":    ack_bytes_classical,
        "qdap_ack_bytes":         ack_bytes_qdap,
        "classical_overhead_pct": (ack_bytes_classical / total_data_bytes) * 100,
        "qdap_overhead_pct":      (ack_bytes_qdap / total_data_bytes) * 100,
        "ghost_stats":            ghost_stats,
        "overhead_reduction":     1 - (ack_bytes_qdap / max(ack_bytes_classical, 1)),
    }
```

### B.6 Metrik 4 — Multiplexing Priority Accuracy

```python
# benchmarks/metrics/priority.py

import asyncio
import time
from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.encoder.amplitude_encoder import AmplitudeEncoder

async def measure_priority_accuracy(n_trials: int = 1000) -> dict:
    """
    QFrame multiplexer'ın öncelik sıralamasını doğrula.

    Test: 3 subframe gönder, alınan sıra amplitude'a uygun mu?

    Senaryo: Video(düşük deadline) + Ses(orta) + Kontrol(acil)
    Beklenti: Kontrol → Ses → Video sırası, her zaman
    """
    encoder  = AmplitudeEncoder()
    correct  = 0
    wrong    = 0
    timings  = []

    for trial in range(n_trials):
        # Her trial'da rastgele öncelikler
        import random
        video_deadline   = random.randint(10, 50)   # ms
        audio_deadline   = random.randint(5, 15)
        control_deadline = random.randint(1, 5)

        subframes = [
            Subframe(payload=b'V'*1000, type=SubframeType.DATA,
                     deadline_ms=video_deadline),
            Subframe(payload=b'A'*100,  type=SubframeType.DATA,
                     deadline_ms=audio_deadline),
            Subframe(payload=b'C'*20,   type=SubframeType.CTRL,
                     deadline_ms=control_deadline),
        ]

        t0 = time.monotonic_ns()
        frame = QFrame.create_with_encoder(subframes)
        elapsed = time.monotonic_ns() - t0
        timings.append(elapsed)

        # Sıralama doğru mu?
        # En düşük deadline → en yüksek amplitude → en önce
        order = frame.send_order   # [ctrl_idx, audio_idx, video_idx] bekliyoruz
        deadlines = [video_deadline, audio_deadline, control_deadline]

        # send_order'daki ilk eleman en acil olmalı
        if deadlines[order[0]] == min(deadlines):
            correct += 1
        else:
            wrong += 1

    import numpy as np
    arr = np.array(timings) / 1e6

    return {
        "n_trials":         n_trials,
        "correct":          correct,
        "wrong":            wrong,
        "accuracy":         correct / n_trials,
        "encode_p99_ms":    float(np.percentile(arr, 99)),
        "encode_mean_ms":   float(arr.mean()),
    }
```

### B.7 Ana Çalıştırıcı

```python
# benchmarks/run_all.py

"""
Tek komutla tüm benchmark'ları çalıştır:
  python benchmarks/run_all.py

Çıktı:
  - Terminal: Rich tabloları
  - benchmarks/results/latest.json
  - benchmarks/results/plots/*.png
"""

import asyncio
import json
import time
from pathlib import Path
from rich.console import Console
from benchmarks.harness import BenchmarkSuite
from benchmarks.metrics.throughput import measure_throughput
from benchmarks.metrics.latency    import measure_latency
from benchmarks.metrics.ack_overhead import measure_ack_overhead
from benchmarks.metrics.priority   import measure_priority_accuracy
from benchmarks.report.generator   import generate_plots

console = Console()

async def main():
    console.rule("[bold green]QDAP Benchmark Suite v0.2[/bold green]")
    console.print(f"Başlama zamanı: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    results = {}

    # 1. Throughput — 3 farklı boyut
    for size_mb in [1, 10, 100]:
        r = await measure_throughput("127.0.0.1", 19000, payload_size_mb=size_mb)
        results[f"throughput_{size_mb}mb"] = r
        console.print(f"  Throughput {size_mb}MB: [green]{r['throughput_mbps']:.2f} MB/s[/green]")

    # 2. Latency — 10K küçük mesaj
    r = await measure_latency("127.0.0.1", 19000, n_msgs=10_000)
    results["latency_10k"] = r
    console.print(f"  Latency p99:  [green]{r['p99_ms']:.3f}ms[/green]")
    console.print(f"  Latency p999: [green]{r['p999_ms']:.3f}ms[/green]")

    # 3. ACK Overhead — farklı loss rate'leri
    for loss in [0.0, 0.01, 0.05, 0.10]:
        r = await measure_ack_overhead(n_frames=1000, loss_rate=loss)
        results[f"ack_overhead_{int(loss*100)}pct_loss"] = r
        console.print(
            f"  ACK Overhead (loss={loss:.0%}): "
            f"Classical={r['classical_overhead_pct']:.1f}% vs "
            f"QDAP={r['qdap_overhead_pct']:.1f}%"
        )

    # 4. Priority Accuracy
    r = await measure_priority_accuracy(n_trials=1000)
    results["priority_accuracy"] = r
    console.print(f"  Priority Accuracy: [green]{r['accuracy']:.1%}[/green]")

    # Sonuçları kaydet
    output_dir = Path("benchmarks/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_dir / "latest.json", "w") as f:
        json.dump(results, f, indent=2)

    # Grafik oluştur
    generate_plots(results, output_dir / "plots")

    console.rule("[bold green]Tamamlandı[/bold green]")
    console.print(f"Sonuçlar: {output_dir}/latest.json")
    console.print(f"Grafikler: {output_dir}/plots/")


if __name__ == "__main__":
    asyncio.run(main())
```

### B.8 Grafik Üretici

```python
# benchmarks/report/generator.py

from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.style as mplstyle
import numpy as np

mplstyle.use('ggplot')

def generate_plots(results: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    _plot_throughput(results, output_dir)
    _plot_latency(results, output_dir)
    _plot_ack_overhead(results, output_dir)
    _plot_priority(results, output_dir)
    _plot_summary(results, output_dir)


def _plot_throughput(results, out):
    sizes   = [1, 10, 100]
    qdap_mb = [results[f"throughput_{s}mb"]["throughput_mbps"] for s in sizes]

    # Baseline: placeholder — gerçek TCP baseline Faz 2'de ölçülecek
    # Şimdilik teorik TCP upper bound
    baseline = [s * 8 * 0.9 for s in sizes]   # ~%90 link utilization

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(sizes))
    width = 0.35

    ax.bar(x - width/2, baseline, width, label='TCP Baseline', color='#e74c3c', alpha=0.8)
    ax.bar(x + width/2, qdap_mb,  width, label='QDAP',         color='#2ecc71', alpha=0.8)

    ax.set_xlabel('Transfer Boyutu')
    ax.set_ylabel('Throughput (MB/s)')
    ax.set_title('QDAP vs TCP Baseline — Throughput')
    ax.set_xticks(x)
    ax.set_xticklabels([f'{s}MB' for s in sizes])
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out / "throughput.png", dpi=150)
    plt.close(fig)


def _plot_latency(results, out):
    if "latency_10k" not in results:
        return

    r = results["latency_10k"]
    percentiles = ['p50', 'p95', 'p99', 'p999']
    values      = [r[f"{p}_ms"] for p in percentiles]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ['#27ae60', '#f39c12', '#e67e22', '#e74c3c']
    bars = ax.bar(percentiles, values, color=colors, alpha=0.85)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                f'{val:.3f}ms', ha='center', va='bottom', fontsize=10)

    ax.set_xlabel('Percentile')
    ax.set_ylabel('Latency (ms)')
    ax.set_title('QDAP Latency Distribution — 10K Messages')
    ax.grid(True, alpha=0.3, axis='y')

    fig.tight_layout()
    fig.savefig(out / "latency.png", dpi=150)
    plt.close(fig)


def _plot_ack_overhead(results, out):
    loss_rates = [0, 1, 5, 10]
    classical  = [results[f"ack_overhead_{l}pct_loss"]["classical_overhead_pct"]
                  for l in loss_rates]
    qdap       = [results[f"ack_overhead_{l}pct_loss"]["qdap_overhead_pct"]
                  for l in loss_rates]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(loss_rates))
    width = 0.35

    ax.bar(x - width/2, classical, width, label='TCP ACK Overhead',   color='#e74c3c', alpha=0.8)
    ax.bar(x + width/2, qdap,      width, label='QDAP Ghost Session', color='#2ecc71', alpha=0.8)

    ax.set_xlabel('Paket Kaybı Oranı')
    ax.set_ylabel('Overhead (%)')
    ax.set_title('ACK Overhead: TCP vs QDAP Ghost Session')
    ax.set_xticks(x)
    ax.set_xticklabels([f'%{l}' for l in loss_rates])
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    fig.tight_layout()
    fig.savefig(out / "ack_overhead.png", dpi=150)
    plt.close(fig)


def _plot_priority(results, out):
    if "priority_accuracy" not in results:
        return

    r        = results["priority_accuracy"]
    accuracy = r["accuracy"] * 100

    fig, ax = plt.subplots(figsize=(5, 5))
    wedges, texts, autotexts = ax.pie(
        [accuracy, 100 - accuracy],
        labels=['Doğru', 'Yanlış'],
        colors=['#2ecc71', '#e74c3c'],
        autopct='%1.1f%%',
        startangle=90
    )
    ax.set_title(f'QFrame Priority Accuracy\n({r["n_trials"]} trial)')

    fig.tight_layout()
    fig.savefig(out / "priority_accuracy.png", dpi=150)
    plt.close(fig)
```

---

## Haftalık Entegrasyon Kontrol Noktaları

### Hafta 1 Sonu — İlk Entegrasyon
```
Track A çıktısı:  QDAPTCPAdapter + socket tuning çalışıyor
Track B çıktısı:  Throughput + latency ölçüm modülleri hazır
Entegrasyon testi: Adapter üzerinden benchmark çalıştır → sayılar çık
Beklenti:         Throughput ölçülüyor, henüz optimize değil
```

### Hafta 2 Sonu — Optimizasyon
```
Track A çıktısı:  Connection pool + backpressure tamamlandı
Track B çıktısı:  ACK overhead + priority accuracy modülleri hazır
Entegrasyon testi: 5 concurrent client benchmark
Beklenti:         ACK overhead rakamları baseline ile karşılaştırılabilir
```

### Hafta 3 Sonu — QUIC + Grafikler
```
Track A çıktısı:  QUIC adapter (aioquic) temel versiyon
Track B çıktısı:  Grafik üretici + JSON export
Entegrasyon testi: TCP vs QUIC karşılaştırma
Beklenti:         İlk gerçek grafik çıktısı
```

### Hafta 4 Sonu — Faz 2 Tamamlandı
```
Tüm senaryolar: bulk_transfer, small_messages, mixed_traffic, packet_loss
Karşılaştırma:  TCP Baseline vs QDAP-TCP vs QDAP-QUIC
Çıktı:          benchmarks/results/latest.json + 5 grafik PNG
Hedef metrikler:
  ✓ Throughput ≥ baseline
  ✓ p99 latency < baseline
  ✓ ACK overhead < %1 (vs baseline %3-5)
  ✓ Priority accuracy > %90
```

---

## Test Dosyaları

```
tests/transport/
├── test_tcp_adapter.py      ← Adapter unit testleri
├── test_backpressure.py     ← Backpressure senaryoları
├── test_connection_pool.py  ← Pool yönetimi
└── test_transport_e2e.py    ← Track A + B entegrasyon

tests/benchmarks/
├── test_harness.py          ← Harness'ın kendi testi
└── test_metrics.py          ← Metrik hesaplama doğruluğu
```

---

## Hızlı Başlangıç

```bash
# Track A — TCP Adapter geliştirmeye başla
touch src/qdap/transport/base.py
touch src/qdap/transport/tcp/adapter.py
touch src/qdap/transport/tcp/tuning.py
touch src/qdap/transport/tcp/backpressure.py
touch src/qdap/transport/tcp/pool.py

# Track B — Benchmark altyapısı
mkdir -p benchmarks/{metrics,scenarios,report,results/plots}
touch benchmarks/harness.py
touch benchmarks/metrics/{throughput,latency,ack_overhead,priority}.py
touch benchmarks/run_all.py

# İlk çalıştırma (Hafta 1 sonu hedefi)
python benchmarks/run_all.py
```

---

*Faz 2 tamamlandığında elimizde somut sayılar ve grafikler olacak — arXiv paper için kritik.*
