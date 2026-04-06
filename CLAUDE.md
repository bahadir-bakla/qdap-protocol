# QDAP — Claude Code Context

Quantum-Inspired Dynamic Application Protocol. Klasik donanımda çalışan,
quantum computing prensiplerinden ilham alan uygulama katmanı protokolü.
Hedef: kriz ortamlarında (yüksek kayıp/gecikme) emergency mesaj teslimi.

---

## Proje Durumu (Phase 13 tamamlandı)

- **388 test PASS** — `python -m pytest tests/ -q`
- **Phase 10–13** tamamlandı (bkz. `PHASE_*.md` dosyaları)
- **pyproject.toml** hazır — `pip install -e ".[dev]"` ile kurulum
- **Rust core** mevcut (`qdap_core/`) — Rust yoksa Python fallback otomatik çalışır

### Kritik benchmark sonuçları (Crisis: 300ms RTT / 35% loss)
```
QDAP          100.0% emergency | 90.4% total | p50=201ms  ★  (Phase 13.2 FEC ile)
HTTP/3 QUIC    73.3% emergency | 69.0% total
HTTP/1.1       69.5% emergency | 63.8% total
gRPC           64.3% emergency | 66.4% total
NATS JetStream 61.0% emergency | 66.0% total
MQTT 3.1.1     19.6% emergency | 57.0% total  ← en kötü
```

### Ablation (Crisis, Phase 13)
```
+QFT Only          +15.3% emergency (Phase 13.1 fix — önceden 0%)
+Priority Only     +30.6% emergency (dominant bileşen)
+FEC Only          +34.1% emergency (Phase 13.2)
+Priority+FEC      +37.6% emergency
Full QDAP          +37.6% emergency (100% emrg, 93.4% total)
```

---

## Mimari

### Kaynak Ağacı
```
src/qdap/
├── __init__.py              # Public API (7 export — Phase 14.1'de genişletilecek)
├── _rust_bridge.py          # Rust/Python seçici: qdap_core varsa Rust, yoksa fallback
├── frame/
│   ├── qframe.py            # QFrame — superposition-inspired multi-payload
│   └── encoder.py           # AmplitudeEncoder — priority→amplitude mapping
├── scheduler/
│   ├── qft_scheduler.py     # QFTScheduler v2 — log-linear softmax + Phase 13.1 emergency
│   ├── session_cache.py     # SessionCache — cihaz profili TTL cache
│   └── strategies.py        # BulkTransfer / LatencyFirst / AdaptiveHybrid
├── session/
│   ├── ghost_session.py     # GhostSession — zero ACK (entanglement-inspired)
│   └── markov.py            # Markov kanal modeli (EMA)
├── broker/
│   ├── ghost_session_adaptive.py  # AdaptiveGhostSession — AIC k=3, BPTT blend
│   └── markov_bptt.py             # BPTTMarkovEstimator — Mini-LSTM (pure Python)
├── transport/
│   ├── fec.py               # AdaptiveFEC — XOR systematic (k,r) code (Phase 13.2)
│   ├── parallel_sender.py   # ParallelSender — 8 concurrent streams
│   └── tcp_adapter.py       # QDAPOverTCP
├── compression/
│   └── delta_encoder.py     # DeltaEncoder — 74.4% boyut azaltması
├── security/
│   ├── session_ticket.py    # SessionTicket — 0-RTT resumption
│   ├── handshake.py         # X25519 ECDH handshake
│   └── key_rotation.py      # AES-256-GCM key rotation
├── chunking/
│   └── adaptive_chunker.py  # Adaptive chunk boyutu
├── verification/            # Qiskit doğrulama (qiskit opsiyonel)
└── server.py                # QDAPServer / QDAPClient — asyncio TCP
```

### Rust Core (`qdap_core/`)
```
qdap_core/src/
├── lib.rs          # PyO3 module registration
├── crypto.rs       # AES-256-GCM, SHA3-256 (SIMD optimize)
├── qframe.rs       # QFrame serialize/deserialize
├── qft_scheduler.rs  # qft_decide, qft_decide_deadline_aware
├── amplitude.rs    # AmplitudeEncoder
├── chunker.rs      # Adaptive chunking
└── x25519.rs       # X25519 key exchange
```

**Build:** `maturin develop --release` (CI'da `continue-on-error: true` — opsiyonel)
**Fallback:** Rust yoksa `_rust_bridge.py` Python implementasyonuna düşer, tüm testler geçer.

---

## Komutlar

```bash
# Kurulum
pip install -e ".[dev]"

# Test
python -m pytest tests/ -q                    # 388 test
python -m pytest tests/examples/ -q           # 21 örnek test
python validate_all.py                         # tam sistem doğrulama

# Benchmarks
python benchmarks/protocol_comparison.py      # 12 protokol × 3 senaryo
python benchmarks/ablation_study.py           # 10 config × 2 senaryo
python benchmarks/statistical_analysis.py     # n=30 Welch t-test

# Rust build (opsiyonel)
cd qdap_core && maturin develop --release

# Demo
python examples/basic_demo.py
```

---

## Tamamlanan Phases

| Phase | Konu | Kritik Dosya |
|-------|------|-------------|
| 10.3  | Session Persistence | `broker/session_store.py` |
| 10.4  | 0-RTT Resumption | `security/session_ticket.py` |
| 10.5  | Parallel Streaming | `transport/parallel_sender.py` |
| 10.6  | Delta Compression | `compression/delta_encoder.py` |
| 11.1  | Protocol Comparison | `benchmarks/protocol_comparison.py` |
| 11.2  | Adaptive Ghost Session | `broker/ghost_session_adaptive.py` |
| 11.6  | Ablation Study | `benchmarks/ablation_study.py` |
| 11.7  | Statistical Significance | `benchmarks/statistical_analysis.py` |
| 12.1  | BPTT Markov (Mini-LSTM) | `broker/markov_bptt.py` |
| 12.2  | Real Server Tests | `benchmarks/real_server_benchmark.py` |
| 12.3  | Reproducibility | `Makefile`, `validate_all.py` |
| 12.4  | Edge Device (emulation) | `tests/edge/` |
| 13.1  | QFT Emergency Fix | `scheduler/qft_scheduler.py` |
| 13.2  | Rate-adaptive FEC | `transport/fec.py` |
| 13.3  | +4 Protocol Benchmarks | `benchmarks/protocol_comparison.py` |

---

## Phase 14 — Kütüphane + Real Network + Video

### 14.1 — Tam Public API (`src/qdap/__init__.py`)
**Ne eksik:** FEC, DeltaEncoder, BPTTMarkovEstimator, AdaptiveGhostSession, ParallelSender,
SessionTicket, AdaptiveFEC public API'de yok.
**Yapılacak:** Tüm kullanışlı sınıfları export et, `py.typed` marker ekle.

### 14.2 — FEC → QDAPServer Pipeline Entegrasyonu
**Ne eksik:** `AdaptiveFEC` standalone; `QDAPServer` encode/decode pipeline'ına bağlı değil.
**Yapılacak:** `QDAPServer(fec_enabled=True)` parametresi, gönderimde otomatik FEC encode,
alımda decode + kayıp recovery.

### 14.3 — Video Streaming Benchmark
**Ne eksik:** `examples/video/` simülasyon; gerçek bitrate adaptation testi yok vs HTTP/3 DASH/WebSocket.
**Yapılacak:** Adaptive bitrate benchmark — 3 senaryo × 4 protokol (QDAP/HTTP/3/WS/gRPC),
throughput + stall oranı + quality switches metrikleri.

### 14.4 — Quickstart Kullanım Örneği
**Ne eksik:** `pip install qdap` yapan biri nasıl emergency mesajı gönderir?
**Yapılacak:** `examples/quickstart.py` — ~30 satırda send/receive + emergency priority demo.

### 14.5 — Real Network Test (tc netem / asyncio loss injection)
**Ne eksik:** Tüm testler simülasyon; gerçek kernel-level loss/delay/jitter yok.
**Yapılacak:** `tests/network/test_real_conditions.py` — asyncio loopback + inject loss/delay
via monkey-patching (macOS'ta tc netem yok, asyncio intercept ile yapılır).

---

## Dikkat Edilecekler

- **`asyncio.sleep` ban:** `benchmarks/statistical_analysis.py`'daki single_run_* fonksiyonları sleep kaldırıldı — latency analitik hesaplanıyor. Tekrar ekleme.
- **MQTT benchmark window:** `bench_mqtt311` / `bench_mqtt50` WINDOW=20 batch gather kullanıyor. Sequential loop yapma.
- **QFT emergency:** `decide_emergency()` methodu var — emergency frame için her zaman MICRO (4KB) chunk döndürür.
- **FEC inline model:** `bench_qdap`'taki `_fec_effective_loss()` importsuz inline hesaplama yapıyor — transport.fec import etme.
- **Rust fallback:** `_rust_bridge.py` otomatik fallback yapıyor, tüm testler Rust olmadan da geçmeli.
- **`README.md` gitignored** — `pyproject.toml`'dan `readme = "README.md"` satırı kaldırıldı.

---

## Test Yapısı

```
tests/
├── test_ablation.py          # Ablation configs (4 async test)
├── test_broker.py            # Broker integration
├── test_convergence.py       # Lemma 1b convergence proof
├── test_markov_bptt.py       # Mini-LSTM (13 test)
├── test_scheduler.py         # QFTScheduler
├── test_statistical.py       # Statistical functions (10 unit test)
├── test_ghost_session_adaptive.py  # AdaptiveGhostSession
├── test_delta_compression.py # DeltaEncoder
├── test_session_resumption.py # 0-RTT
├── test_parallel_streaming.py # ParallelSender
├── examples/
│   ├── test_video_server.py  # Video stream tests
│   └── test_iot_sensor.py    # IoT sensor tests
└── edge/
    ├── memory_footprint.py   # tracemalloc (QFT ~24KB, Ghost ~5KB)
    └── cpu_profile.py        # QFT 374k decisions/s, Pi÷4=93k/s
```

---

## Kütüphane Haline Getirme Durumu

### Hazır
- `pyproject.toml` — hatchling build, MIT lisans, Python ≥3.11
- `py.typed` — **Phase 14.1'de eklenecek**
- GitHub Actions CI — ubuntu + macos, Python 3.11/3.12

### Eksik (Phase 14 sonrası tamamlanacak)
- PyPI publish workflow (`publish.yml`)
- Type stubs (`*.pyi`)
- Changelog (`CHANGELOG.md`)
- Quickstart dökümanı

---

## Önemli Metrikler

| Metrik | Değer |
|--------|-------|
| Emergency delivery (Crisis) | **100.0%** (QDAP) vs 73.3% (HTTP/3) |
| Total delivery (Crisis) | **90.4%** (QDAP) vs 69.0% (HTTP/3) |
| Convergence | t* = 29 adım (Lemma 1b, lr=0.15, ε=0.01) |
| Ghost Session F1 | F1(0.01) = 0.9999 |
| Delta compression | **74.4%** boyut azaltması |
| Parallel streaming | **7.7×** speedup (8 stream) |
| 0-RTT resumption | **2.8×** speedup |
| AWS WAN 64KB | **14×** speedup (Ireland↔Singapore) |
| Edge footprint | QFT ~24KB, Ghost ~5KB/conn |
| FEC improvement | **8.16×** (Emergency, 35% loss) |
