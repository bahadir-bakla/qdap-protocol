# QDAP — Quantum-Inspired Adaptive Protocol

> **Ghost Session** · **Zero ACK Overhead** · **Deadline-Aware Priority** · **Forward Secrecy**

[![Tests](https://github.com/bahadir-bakla/qdap-protocol/actions/workflows/test.yml/badge.svg)](https://github.com/bahadir-bakla/qdap-protocol/actions)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://rustup.rs)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

QDAP is a quantum-inspired application-layer protocol that eliminates ACK overhead through **Ghost Session** — achieving **110× throughput improvement** for small payloads compared to classical request-response protocols.

## Key Results

| Payload | Classical TCP | QDAP (Ghost) | Speedup |
|---------|--------------|--------------|---------|
| 1KB     | 0.31 Mbps    | 34.6 Mbps    | **110×** |
| 1MB     | 7.92 Mbps    | 7.94 Mbps    | 1.0×    |
| 10MB    | 8.25 Mbps    | 9.16 Mbps    | 1.11×   |
| 100MB   | 7.70 Mbps    | 7.86 Mbps    | 1.02×   |

**vs MQTT (IoT scenario):**
- Emergency deadline hit: MQTT **0%** → QDAP **100%**
- Connections: MQTT **100** → QDAP **1**
- ACK bytes: MQTT **4000B** → QDAP **0B**

## Architecture

```
┌─────────────────────────────────────────────┐
│              QDAP Stack                      │
├─────────────────────────────────────────────┤
│  Ghost Session     │  Zero application ACK  │
│  QFT Scheduler     │  Probe-free adaptive   │
│  AmplitudeEncoder  │  Deadline-aware prio   │
│  QFrame Batch      │  8× hash reduction     │
├─────────────────────────────────────────────┤
│  Security Layer    │  X25519 + AES-256-GCM  │
│  Key Rotation      │  Forward Secrecy       │
├─────────────────────────────────────────────┤
│  Rust Hot-Path     │  SHA3, AES-NI, L2-norm │
├─────────────────────────────────────────────┤
│  Transport         │  TCP · QUIC · Loopback │
└─────────────────────────────────────────────┘
```

## Installation

```bash
git clone https://github.com/bahadir-bakla/qdap-protocol
cd qdap-protocol
pip install -r requirements.txt

# Optional: Rust hot-path (requires Rust toolchain)
pip install maturin
cd qdap_core && maturin develop --release
```

## Tests

```bash
pytest tests/ -v
# 224 tests, ~8s
```

## Benchmarks

```bash
# Docker benchmark (requires Docker)
cd docker_benchmark
docker compose up --build

# Results in docker_benchmark/results/
```

## Paper

> *QDAP: A Quantum-Inspired Adaptive Protocol for Zero-Overhead Application-Layer Communication*
> arXiv preprint (coming soon)

## License

MIT
