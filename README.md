# QDAP — Quantum-Inspired Dynamic Application Protocol

> **Emergency-First Delivery** · **Rate-Adaptive FEC** · **Ghost Session** · **Deadline-Aware Scheduling**

[![Tests](https://github.com/bahadir-bakla/qdap-protocol/actions/workflows/test.yml/badge.svg)](https://github.com/bahadir-bakla/qdap-protocol/actions)
[![PyPI](https://img.shields.io/pypi/v/qdap.svg)](https://pypi.org/project/qdap/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://rustup.rs)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Website:** [qdap.dev](https://qdap.dev)

---

## What is QDAP?

QDAP is a **pure-Python, Rust-accelerated application-layer protocol** built for environments where classical protocols break down — disaster zones, satellite links, congested mobile networks.

Unlike MQTT, HTTP/3 or gRPC, QDAP treats emergency messages as **first-class citizens at the protocol level**, not as an application concern.

### Real AWS WAN results (Ireland ↔ Singapore, 30% packet loss injected)

```
Crisis scenario — messages delivered within 500ms deadline:

  QDAP        ████████████████████  99.0%  ← fire-and-forget, no HOL blocking
  HTTP/1.1    █████████████▊        68.8%  retransmits everything, misses deadline
  WebSocket   █████████████▍        66.0%  HOL blocks emergency messages
  HTTP/2      ███████████           55.0%  worst deadline performance
```

```
Normal WAN throughput (1KB messages, Ireland ↔ Singapore):

  QDAP        ████████████████████  20.8 Mbps
  HTTP/1.1                          0.05 Mbps  → QDAP 99× higher throughput
  LargeFile   ████████████████████  76.7 Mbps  (10MB transfer)
```

### Real WiFi LAN results (two physical machines, same AP)

```
LAN p99 latency:   QDAP 21.4ms  vs  gRPC 230ms  →  10.7× lower
LAN throughput:    QDAP 20.8 Mbps  vs  gRPC 0.39 Mbps  →  53× higher
Within 50ms:       QDAP 99.8%  vs  HTTP/1.1 92.8%
```

---

## Installation

```bash
pip install qdap
```

**From source:**
```bash
git clone https://github.com/bahadir-bakla/qdap-protocol.git
cd qdap-protocol
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
from qdap import QDAPServer, QDAPClient, AdaptiveFEC, DeltaEncoder

# Emergency-priority message
fec = AdaptiveFEC()
fec.observe_loss(lost=7, sent=20)          # 35% channel loss
packets, profile = fec.encode(b"SOS: evacuation needed", is_emergency=True)
print(f"{profile.label} — {len(packets)} coded packets")

# Run a server
server = QDAPServer("0.0.0.0", 19876)
await server.start()

# Connect from another device
async with QDAPClient("192.168.1.50", 19876) as client:
    await client.send_multiframe(
        payloads=[b"[EMERGENCY] Zone 4 fire"],
        deadline_ms=[50.0],
    )
```

### Two-device LAN demo

```bash
# Device A (server) — run first
python examples/lan_demo.py server

# Device B (client) — replace IP with Device A's LAN IP
python examples/lan_demo.py client 192.168.1.50
```

More examples in `examples/` — see `quickstart.py`, `lan_demo.py`, `iot/`, `video/`.

---

## Key Results

| Metric | Value |
|---|---|
| Emergency delivery <500ms (real AWS crisis) | **99.0%** vs 68.8% (HTTP/1.1) |
| LAN throughput (real WiFi, two machines) | **20.8 Mbps** vs 0.39 Mbps (gRPC) → **53×** |
| LAN p99 latency | **21.4ms** vs 230ms (gRPC) → **10.7×** lower |
| WAN large file throughput (Ireland↔Singapore) | **76.7 Mbps** |
| FEC improvement (Emergency, 35% loss) | **8.16×** |
| Ghost Session F1 | **0.9999** at 1% loss |
| Delta compression | **74.4%** bandwidth reduction |
| Parallel streaming speedup | **7.7×** (8 streams) |
| 0-RTT resumption | **2.8×** reconnect speedup |
| QFT convergence | t* = **29 steps** (Lemma 1b) |
| Test suite | **444 tests**, all pass |

---

## Architecture

```
src/qdap/
├── frame/          QFrame superposition multiplexing, AmplitudeEncoder
├── scheduler/      QFTScheduler (log-linear softmax, 374k decisions/s)
├── session/        GhostSession zero-ACK delivery, Markov channel model
├── broker/         AdaptiveGhostSession, BPTTMarkovEstimator (mini-LSTM)
├── transport/      AdaptiveFEC, ParallelSender, TCP adapter
├── compression/    DeltaEncoder (74.4% reduction)
├── security/       X25519 ECDH, AES-256-GCM, SessionTicket 0-RTT
└── server.py       QDAPServer / QDAPClient (asyncio TCP)

qdap_core/          Rust hot-path via PyO3
└── src/            crypto.rs, qft_scheduler.rs, qframe.rs, fec.rs, delta.rs
```

---

## Running Tests

```bash
python -m pytest tests/ -q                   # 444 tests
python -m pytest tests/verification/ -q      # Qiskit circuit equivalence (optional)
python validate_all.py                        # full system validation
```

---

## Benchmarks

```bash
python benchmarks/wan_server_v2.py           # start benchmark server (7 protocols)
python benchmarks/wan_client_v2.py <IP>      # WAN benchmark client
python benchmarks/lan_benchmark.py <IP>      # LAN two-machine benchmark
python benchmarks/protocol_comparison.py     # 12 protocols × 3 scenarios
python benchmarks/ablation_study.py          # component contribution analysis
```

---

## License

MIT · [qdap.dev](https://qdap.dev)
