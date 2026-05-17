# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Install locally (pure Python, no Rust)
pip install -e ".[dev]"

# Build Rust hot-path (optional — ~3× crypto speedup via AES-NI)
cd qdap_core && maturin develop --release && cd ..

# Run all tests
pytest tests/ -v --tb=short -q

# Run tests excluding slow statistical tests
pytest tests/ -x --tb=short -q --ignore=tests/test_statistical.py

# Run a single test file
pytest tests/test_fec.py -v

# Run tests with coverage
pytest tests/ --cov=src/qdap --cov-report=html --tb=short

# V2X benchmark (simulation)
cd simulations/v2x
python run_benchmark.py --quick          # ~2 min, smoke test
python run_benchmark.py                  # ~30 min, full 450 combinations
python run_benchmark.py --scenario urban # single scenario
python generate_report.py                # regenerate combined PDF report
```

## Architecture

### Dual-layer implementation

Every performance-critical component has two implementations that coexist:

1. **`src/qdap/`** — Pure Python (always works, fallback)
2. **`qdap_core/src/`** — Rust via PyO3 (optional, auto-detected at runtime)

The bridge between them is `src/qdap/_rust_bridge.py`. All Python modules try `import qdap_core` and silently fall back. Never break this pattern — the library must work without Rust.

### Core protocol stack (`src/qdap/`)

| Module | Role |
|---|---|
| `frame/` | QFrame serialization — superposition-inspired multi-payload framing |
| `scheduler/` | QFT Scheduler — log-linear softmax priority (emergency jumps queue) |
| `security/` | AES-GCM encryption, X25519 key exchange, HMAC integrity |
| `session/` | GhostSession — 0-RTT reconnect, session token persistence |
| `transport/` | TCP/UDP wire layer |
| `compression/` | DeltaEncoder — position/telemetry delta compression (74.4% BSM reduction) |
| `protocol/` | QDAPClient / QDAPServer — top-level async API |
| `broker/` | Message routing and priority queue |

### Rust core (`qdap_core/src/`)

| File | Implements |
|---|---|
| `fec.rs` | XOR systematic (k,r) FEC — exact binomial loss model |
| `qft_scheduler.rs` | QFT-inspired scheduling, 2.6M decisions/sec |
| `crypto.rs` | AES-256-GCM with AES-NI |
| `delta.rs` | Delta encoding for telemetry streams |
| `ghost_session.rs` | HMAC session tokens for 0-RTT resumption |
| `qframe.rs` | Zero-copy frame serialization |

### V2X simulation (`simulations/v2x/`)

Discrete-event Monte Carlo simulation comparing QDAP vs DSRC/802.11p, 802.11bd, C-V2X Mode 4, UDP, MQTT.

| File | Role |
|---|---|
| `simulation.py` | Main loop — 50ms timestep, vectorized N×N distance matrix |
| `protocols.py` | Statistical models for each protocol's deliver() — uses real `qdap_core` FEC if available |
| `channel.py` | Two-ray LOS + WINNER+ B1 NLOS channel model (ETSI TR 102 861) |
| `agents.py` | Vehicle/pedestrian kinematics |
| `messages.py` | BSM (SAE J2735) and DENM (ETSI EN 302 637) message types |
| `run_benchmark.py` | CLI entry point — produces `results/v2x_results.csv` |
| `plots.py` | Matplotlib figures → `results/v2x_benchmark.pdf` |
| `generate_report.py` | Combined 7-page PDF (brief + all figures) |

Results are fully reproducible: `seed=42`, 5 runs per combination, 450 total.

## Key Design Decisions

**FEC model**: `P(fail) = P(>r losses in k+r packets)` — exact binomial via `qdap_core.fec_effective_loss(per, k, r)`. Emergency profile: k=1, r=2 (any 1 of 3 sufficient). Do not revert to the `per^k` approximation.

**Emergency injection in simulation**: Deterministic (fixed step indices at 25% and 65% of sim duration), identical across all protocols. This ensures fair comparison — never use random per-step probability.

**DSRC collision model**: `p_col = min(cbr² × 0.6, 0.55)` — empirical broadcast model (not Bianchi, which is for unicast). Bianchi gives unrealistically high collision rates for broadcast.

**CBR**: Always use local neighbor count (agents within range), not global N.

## Testing Notes

`tests/test_statistical.py` contains slow multi-run statistical tests — excluded from fast runs. The `tests/network/` and `tests/real_servers/` directories require live server instances. Run the standard suite with `pytest tests/ -x -q --ignore=tests/test_statistical.py --ignore=tests/network --ignore=tests/real_servers`.
