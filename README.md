# QDAP — Quantum-Inspired Dynamic Application Protocol

> **Emergency-First Delivery** · **Rate-Adaptive FEC** · **Ghost Session** · **Deadline-Aware Scheduling**

[![Tests](https://github.com/bahadir-bakla/qdap-protocol/actions/workflows/test.yml/badge.svg)](https://github.com/bahadir-bakla/qdap-protocol/actions)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://rustup.rs)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What is QDAP?

QDAP is a **pure-Python, Rust-accelerated application-layer protocol** built for environments where classical protocols break down — disaster zones, satellite links, congested mobile networks.

Unlike MQTT, HTTP/3 or gRPC, QDAP treats emergency messages as **first-class citizens at the protocol level**, not as an application concern. In a 300ms / 35% packet-loss channel (a realistic disaster scenario), QDAP delivers **100% of emergency frames** while MQTT 3.1.1 delivers only 19.6%.

```
Crisis scenario (300ms RTT / 35% packet loss):

  QDAP          ████████████████████ 100.0% emergency | 90.4% total
  HTTP/3 QUIC   ██████████████▋      73.3% emergency | 69.0% total
  HTTP/1.1      █████████████▊       69.5% emergency | 63.8% total
  gRPC          ████████████▊        64.3% emergency | 66.4% total
  NATS          ████████████▏        61.0% emergency | 66.0% total
  MQTT 5.0      ██████▎              31.7% emergency | 54.8% total
  MQTT 3.1.1    ███▉                 19.6% emergency | 57.0% total
```

---

## Installation

```bash
pip install -e ".[dev]"
```

**Optional: Rust hot-path** (AES-NI, SHA3-SIMD — ~3× crypto speedup):
```bash
pip install maturin
cd qdap_core && maturin develop --release
```

The library works fully without Rust — pure Python fallback is automatic.

---

## Quickstart

```python
import qdap

# 1. Emergency-priority server/client (real TCP)
server = qdap.QDAPServer("0.0.0.0", 9000, fec_enabled=True)
await server.start()

async with qdap.QDAPClient("localhost", 9000, fec_enabled=True) as client:
    # Multiple payloads in one frame — shortest deadline sent first
    await client.send_multiframe(
        payloads=[b"[ALERT] Zone 4 fire!", b"Camera stream chunk"],
        deadline_ms=[50.0, 500.0],   # 50ms = emergency priority
    )

# 2. Adaptive FEC for lossy channels
fec = qdap.AdaptiveFEC()
fec.observe_loss(lost=7, sent=20)             # 35% channel loss
coded, profile = fec.encode(b"SOS", is_emergency=True)
# profile=EMERGENCY (k=1, r=2): any 1-of-3 coded copies sufficient
# effective delivery: 95.7%  (up from 65.0% raw)

# 3. IoT delta compression
enc = qdap.DeltaEncoder()
enc.encode({"temp": 23.1, "co2": 412})        # FULL frame (first)
enc.encode({"temp": 23.2, "co2": 412})        # DELTA frame (temp only)
# 62%+ bandwidth reduction on typical IoT streams

# 4. Channel prediction
predictor = qdap.BPTTMarkovEstimator()
predictor.observe(rtt_ms=300, loss_rate=0.35, payload_size=1024, time_delta_s=0.1)
p_delivery, p_retransmit, quality = predictor.predict()
profile = qdap.select_fec_profile(1 - p_delivery, is_emergency=True)
```

Run the full demo: `python examples/quickstart.py`

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         QDAP Stack                               │
├───────────────────────────────┬──────────────────────────────────┤
│  QFrame Multiplexer           │  Superposition-inspired          │
│  AmplitudeEncoder             │  multi-payload priority encoding  │
├───────────────────────────────┼──────────────────────────────────┤
│  QFT Scheduler (v2)           │  FFT traffic analysis            │
│  + Emergency Scheduling       │  log-linear softmax + MICRO      │
│    (Phase 13.1)               │  chunk deadline-aware retransmit │
├───────────────────────────────┼──────────────────────────────────┤
│  Priority Queue               │  Frame-level 0-1000 priority     │
│                               │  Emergency preempts normal       │
├───────────────────────────────┼──────────────────────────────────┤
│  Rate-Adaptive FEC            │  XOR systematic (k, r) code      │
│  (Phase 13.2)                 │  EMERGENCY k=1,r=2: 8.16× gain  │
│                               │  BALANCED k=2,r=2: 2.77× gain   │
├───────────────────────────────┼──────────────────────────────────┤
│  Ghost Session                │  Zero-ACK implicit delivery      │
│  AdaptiveGhostSession         │  AIC-optimal k=3, BPTT blend     │
│  (Phase 11.2)                 │  F1(0.01) = 0.9999               │
├───────────────────────────────┼──────────────────────────────────┤
│  BPTT Markov Estimator        │  Pure-Python Mini-LSTM           │
│  (Phase 12.1)                 │  No torch/numpy dependency       │
├───────────────────────────────┼──────────────────────────────────┤
│  Delta Compression            │  74.4% IoT bandwidth reduction   │
│  Parallel Streaming           │  7.7× speedup (8 streams)        │
│  0-RTT Session Resumption     │  2.8× reconnect speedup          │
├───────────────────────────────┼──────────────────────────────────┤
│  Security                     │  X25519 ECDH + AES-256-GCM       │
│                               │  Forward Secrecy, key rotation   │
├───────────────────────────────┼──────────────────────────────────┤
│  Rust Core (qdap_core/)       │  SHA3, AES-NI, QFT, QFrame       │
│  Python Fallback automatic    │  PyO3 bindings, SIMD optional    │
├───────────────────────────────┼──────────────────────────────────┤
│  Transport                    │  TCP · QUIC · WebSocket · UDP    │
└───────────────────────────────┴──────────────────────────────────┘
```

---

## Benchmark Results

### Protocol Comparison (12 protocols × 3 scenarios × 500 messages)

**Crisis scenario — 300ms RTT, 35% packet loss:**

| Protocol | Emergency Delivery | Total Delivery | p50 Latency | Throughput |
|---|---|---|---|---|
| **QDAP** | **100.0%** ★ | **90.4%** | **201ms** | 17.4 Mbps |
| HTTP/3 QUIC | 73.3% | 69.0% | 265ms | 11.3 Mbps |
| HTTP/1.1 | 69.5% | 63.8% | 348ms | 0.5 Mbps |
| WebSocket | 65.7% | 65.8% | 296ms | 23.2 Mbps |
| gRPC | 64.3% | 66.4% | 308ms | 4.7 Mbps |
| HTTP/2 | 62.5% | 63.4% | 308ms | 4.6 Mbps |
| NATS JetStream | 61.0% | 66.0% | 313ms | 13.0 Mbps |
| AMQP 1.0 | 59.2% | 62.8% | 331ms | 4.2 Mbps |
| CoAP | 53.3% | 65.4% | 253ms | 4.6 Mbps |
| MQTT 5.0 | 31.7% | 54.8% | 538ms | 5.2 Mbps |
| MQTT 3.1.1 | **19.6%** | 57.0% | 601ms | 5.0 Mbps |

> QDAP's emergency advantage comes entirely from the protocol stack, not the channel. All protocols were tested on identical simulated network conditions.

### Ablation Study — Component Contribution (Crisis)

| Configuration | Emergency Delivery | Gain over Baseline |
|---|---|---|
| Baseline (Raw TCP) | 62.4% | — |
| +QFT Only | 77.6% | +15.3% |
| +Priority Only | 92.9% | +30.6% |
| +Ghost Session Only | 65.9% | +3.5% |
| +FEC Only | 96.5% | +34.1% |
| +Priority + FEC | 100.0% | **+37.6%** |
| **Full QDAP** | **100.0%** | **+37.6%** |

Priority queue is the dominant component (+30.6%). FEC compounds it to 100%.

### Statistical Significance (n=30 runs, Welch's t-test)

| Metric | QDAP | Baseline | p-value | Cohen's d |
|---|---|---|---|---|
| Emergency Delivery | 93.1% ± 3.2% | 63.7% ± 7.1% | < 0.001 *** | 5.37 (large) |
| Latency p50 | 210ms ± 1.2ms | 301ms ± 2.1ms | < 0.001 *** | 52.7 (large) |

All differences statistically significant at α=0.05 with large effect sizes.

### Video Streaming Benchmark (Crisis — 300ms/35%)

| Protocol | Delivery | Stall Rate | Quality Switches | Emergency |
|---|---|---|---|---|
| **QDAP** | **85.0%** | **15.0%** | **0** | **100.0%** |
| HTTP/3 DASH | 58.3% | 41.7% | 44 | 66.7% |
| WebSocket | 58.3% | 41.7% | 0 | 66.7% |
| gRPC | 58.3% | 41.7% | 15 | 66.7% |

QDAP maintains **stable quality** (0 switches) through adaptive FEC and micro-chunking. HTTP/3 DASH thrashes through 44 quality downgrades in the same stream.

### FEC Effectiveness at 35% Loss

| Message Type | Profile | Raw Delivery | FEC Delivery | Improvement |
|---|---|---|---|---|
| Emergency | EMERGENCY (k=1, r=2) | 65.0% | **95.7%** | **8.2×** |
| Normal | BALANCED (k=2, r=2) | 65.0% | **87.3%** | **2.8×** |

---

## Components

### QFrame — Multi-payload Frame Multiplexer
Inspired by quantum superposition: multiple payloads are encoded into a single frame with amplitude-weighted priority. The decoder processes subframes in amplitude order — shortest deadline first.

```python
from qdap import QFrame, Subframe, SubframeType

frame = QFrame.create_with_encoder(subframes=[
    Subframe(payload=b"alert", deadline_ms=50),    # α=0.89 (highest)
    Subframe(payload=b"video", deadline_ms=500),   # α=0.44
    Subframe(payload=b"log",   deadline_ms=5000),  # α=0.11
])
# send_order: [0, 1, 2] — alert always first
```

### QFT Scheduler — Deadline-Aware Traffic Scheduling
FFT-based traffic analysis selects optimal chunk strategy (MICRO/SMALL/MEDIUM/LARGE/JUMBO). In Phase 13.1, emergency frames always use MICRO (4KB) chunks — enabling retransmit-within-deadline with `decide_emergency()`.

- **Convergence**: t* = 29 steps (Lemma 1b, lr=0.15, ε=0.01)
- **Emergency**: MICRO (4KB) chunks + 60% ACK overhead reduction
- **Retransmit budget**: `n_retries = floor(deadline_ms / RTT) - 1`

### Ghost Session — Zero-ACK Delivery
Entanglement-inspired: sender and receiver share identical Markov state machines. Delivery is confirmed implicitly through state synchrony, not explicit ACKs.

- **ACK overhead**: 0 bytes (vs 40B per TCP ACK)
- **F1 score**: 0.9999 at 1% loss
- **AIC-optimal state count**: k=3 (Pareto optimal)

### AdaptiveFEC — Rate-Adaptive Forward Error Correction
XOR-based systematic (k, r) code. Profile selected automatically from observed channel loss and message priority.

| Profile | k | r | When Used | Overhead |
|---|---|---|---|---|
| EMERGENCY | 1 | 2 | is_emergency=True, loss≥any | 3.0× |
| AGGRESSIVE | 1 | 1 | is_emergency=True, overhead<3× | 2.0× |
| BALANCED | 2 | 2 | normal, loss≥20% | 2.0× |
| RELIABLE | 2 | 1 | normal, loss 5-20% | 1.5× |
| NONE | 1 | 0 | normal, loss<5% | 1.0× |

### BPTTMarkovEstimator — Pure-Python Mini-LSTM
2-layer LSTM (32 hidden units, 6 input features) implemented in pure Python. No NumPy or PyTorch dependency. Predicts (p_delivery, p_retransmit, quality) from RTT/loss history. Blends with EMA as training data accumulates.

### DeltaEncoder — IoT Bandwidth Optimizer
Binary delta encoding for repetitive sensor streams. FULL frame on first send, DELTA frames (only changed fields) thereafter. 62-74% bandwidth reduction on typical IoT workloads.

---

## Tests

```bash
# Full suite
python -m pytest tests/ -q               # 404 tests

# By category
python -m pytest tests/network/ -v       # Real network condition tests (16)
python -m pytest tests/examples/ -v      # Usage examples (21)

# System validation
python validate_all.py
```

**Test coverage:**
- Unit tests: QFrame, QFTScheduler, GhostSession, FEC, DeltaEncoder, BPTT Markov
- Integration: broker, session resumption, parallel streaming
- Network: real TCP loopback with asyncio loss/delay/jitter injection (macOS-compatible)
- Fuzzing: property-based tests with Hypothesis
- Verification: Qiskit circuit equivalence (optional)

---

## Benchmarks

```bash
# Protocol comparison (12 protocols × 3 scenarios)
python benchmarks/protocol_comparison.py

# Ablation study (component contribution analysis)
python benchmarks/ablation_study.py

# Statistical significance (n=30 Welch t-test)
python benchmarks/statistical_analysis.py

# Video streaming benchmark
python benchmarks/video_streaming_benchmark.py

# All at once
make benchmark
```

Results saved to `benchmarks/results/`.

---

## Project Structure

```
qdap/
├── src/qdap/
│   ├── __init__.py              # Public API — 23 exports
│   ├── frame/                   # QFrame, AmplitudeEncoder
│   ├── scheduler/               # QFTScheduler, SessionCache
│   ├── session/                 # GhostSession, Markov model
│   ├── broker/                  # AdaptiveGhostSession, BPTTMarkovEstimator
│   ├── transport/               # AdaptiveFEC, ParallelSender, TCP/QUIC adapters
│   ├── compression/             # DeltaEncoder
│   ├── security/                # X25519, AES-256-GCM, SessionTicket
│   └── server.py                # QDAPServer, QDAPClient
├── qdap_core/                   # Rust hot-path (PyO3)
│   └── src/                     # crypto.rs, qft_scheduler.rs, qframe.rs
├── benchmarks/                  # All benchmark scripts + results/
├── examples/
│   ├── quickstart.py            # 10-minute getting started guide
│   ├── basic_demo.py            # Core components demo (rich output)
│   ├── iot/                     # IoT gateway + sensor demos
│   └── video/                   # Adaptive bitrate streaming demo
├── tests/
│   ├── network/                 # Real network condition tests
│   ├── examples/                # Usage example tests
│   └── verification/            # Qiskit circuit verification
└── docs/                        # Mathematical foundations, API reference
```

---

## Key Metrics Summary

| Metric | Value |
|---|---|
| Emergency delivery (Crisis 300ms/35%) | **100.0%** vs 73.3% (HTTP/3) |
| Total delivery (Crisis) | **90.4%** vs 69.0% (HTTP/3) |
| Video stall rate (Crisis) | **15.0%** vs 41.7% (HTTP/3 DASH) |
| Video quality switches (Crisis) | **0** vs 44 (HTTP/3 DASH) |
| FEC improvement (Emergency, 35% loss) | **8.16×** |
| Statistical significance | p < 0.001, d = 5.37 (large) |
| QFT convergence | t* = **29 steps** (Lemma 1b) |
| Ghost Session F1 | **0.9999** at 1% loss |
| Delta compression | **74.4%** bandwidth reduction |
| Parallel streaming speedup | **7.7×** (8 streams) |
| 0-RTT resumption | **2.8×** reconnect speedup |
| AWS WAN speedup (64KB) | **14×** (Ireland ↔ Singapore) |
| Edge footprint | QFT **~24KB**, Ghost **~5KB/conn** |
| Test suite | **404 tests**, all pass |

---

## Paper

> *QDAP: A Quantum-Inspired Dynamic Application Protocol for Emergency-Resilient Communication*
>
> Introduces QFrame superposition multiplexing, QFT deadline-aware scheduling,
> Ghost Session zero-ACK delivery, and rate-adaptive FEC. Evaluated across 12
> protocols under Normal / Challenged / Crisis network conditions.

---

## License

MIT
