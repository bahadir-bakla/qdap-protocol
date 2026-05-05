"""
qdap — command-line interface

Usage:
    qdap info                        show version, backend, capabilities
    qdap ping <host> [port]          send emergency frame, measure RTT
    qdap bench [--quick]             local throughput + FEC + scheduler bench
    qdap diagnose                    check environment and dependencies
"""

from __future__ import annotations

import asyncio
import sys
import time
import os


def _color(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


def _ok(s: str) -> str:   return _color(s, "92")
def _warn(s: str) -> str: return _color(s, "93")
def _err(s: str) -> str:  return _color(s, "91")
def _bold(s: str) -> str: return _color(s, "1")
def _dim(s: str) -> str:  return _color(s, "2")


# ─────────────────────────────────────────────────────────────────────────────
# qdap info
# ─────────────────────────────────────────────────────────────────────────────

def cmd_info() -> None:
    import qdap
    from qdap._rust_bridge import backend_info

    bi = backend_info()
    backend_label = _ok("Rust (SIMD-accelerated)") if bi["rust_available"] else _warn("Pure Python")

    print()
    print(_bold("QDAP — Quantum-Inspired Dynamic Application Protocol"))
    print(f"  version   : {_bold(qdap.__version__)}")
    print(f"  backend   : {backend_label}")
    print(f"  python    : {sys.version.split()[0]}")
    print()
    print(_bold("Core modules:"))
    _check_import("  QFrame / QFTScheduler",    "qdap.frame.qframe",        "QFrame")
    _check_import("  AdaptiveFEC",               "qdap.transport.fec",       "AdaptiveFEC")
    _check_import("  GhostSession",              "qdap.session.ghost_session","GhostSession")
    _check_import("  BPTTMarkovEstimator",       "qdap.broker.markov_bptt",  "BPTTMarkovEstimator")
    _check_import("  DeltaEncoder",              "qdap.compression.delta_encoder", "DeltaEncoder")
    _check_import("  ParallelSender",            "qdap.transport.parallel_sender", "ParallelSender")
    _check_import("  SessionTicket",             "qdap.security.session_ticket",   "SessionTicket")
    _check_import("  ChannelStamp",              "qdap.protocol.channel_stamps",   "ChannelStamp")
    _check_import("  DecisionDAG",               "qdap.protocol.decision_dag",     "DecisionDAG")
    print()
    print(_bold("Optional extras:"))
    _check_import_opt("  matplotlib (viz)",  "matplotlib")
    _check_import_opt("  qiskit (quantum)",  "qiskit")
    _check_import_opt("  aioquic (QUIC)",    "aioquic")
    print()
    print(_dim("  pip install qdap[viz]    — add matplotlib/rich"))
    print(_dim("  pip install qdap[qiskit] — add Qiskit verification"))
    print(_dim("  pip install qdap[quic]   — add QUIC/HTTP3 transport"))
    print()


def _check_import(label: str, module: str, attr: str) -> None:
    try:
        m = __import__(module, fromlist=[attr])
        getattr(m, attr)
        print(f"{label:40s} {_ok('ok')}")
    except Exception as e:
        print(f"{label:40s} {_err('MISSING')}  ({e})")


def _check_import_opt(label: str, module: str) -> None:
    try:
        __import__(module)
        print(f"{label:40s} {_ok('installed')}")
    except ImportError:
        print(f"{label:40s} {_dim('not installed')}")


# ─────────────────────────────────────────────────────────────────────────────
# qdap ping
# ─────────────────────────────────────────────────────────────────────────────

def cmd_ping(host: str, port: int, count: int = 4) -> None:
    asyncio.run(_ping_async(host, port, count))


async def _ping_async(host: str, port: int, count: int) -> None:
    from qdap.server import QDAPServer, QDAPClient

    print()
    print(f"  QDAP ping  {_bold(host)}:{_bold(str(port))}  ({count} emergency frames × 1KB)")
    print()

    rtts: list[float] = []
    errors = 0

    for i in range(count):
        try:
            t0 = time.perf_counter()
            async with QDAPClient(host, port) as client:
                payload = f"PING seq={i} ts={t0:.6f}".encode().ljust(1024, b"\x00")
                await client.send_multiframe(
                    payloads=[payload],
                    deadline_ms=[50.0],   # emergency deadline
                )
            rtt_ms = (time.perf_counter() - t0) * 1000
            rtts.append(rtt_ms)
            marker = _ok("ok") if rtt_ms < 100 else _warn("ok")
            print(f"  seq={i}  rtt={rtt_ms:7.2f}ms  {marker}")
        except Exception as e:
            errors += 1
            print(f"  seq={i}  {_err('ERR')}  {e}")
        await asyncio.sleep(0.05)

    if rtts:
        import statistics
        print()
        print(f"  --- {host}:{port} ping statistics ---")
        print(f"  {count} sent, {len(rtts)} received, {errors} errors")
        print(f"  rtt min/avg/max = "
              f"{min(rtts):.2f}/{statistics.mean(rtts):.2f}/{max(rtts):.2f} ms")
        if len(rtts) > 1:
            jitter = statistics.stdev(rtts)
            print(f"  jitter (stdev)  = {jitter:.2f} ms")
    else:
        print()
        print(_err("  All pings failed — is the QDAP server running?"))
        print(_dim(f"  Start one with: python -m qdap.server --host {host} --port {port}"))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# qdap bench
# ─────────────────────────────────────────────────────────────────────────────

def cmd_bench(quick: bool = False) -> None:
    asyncio.run(_bench_async(quick))


async def _bench_async(quick: bool) -> None:
    print()
    print(_bold("QDAP local benchmark"))
    print(_dim("  loopback only — for real-network results see benchmarks/wan_client.py"))
    print()

    # 1. Rust backend speed
    _bench_rust()

    # 2. Scheduler throughput
    _bench_scheduler(n=50_000 if not quick else 5_000)

    # 3. FEC improvement
    _bench_fec()

    # 4. Delta compression
    _bench_delta()

    # 5. Server round-trip (loopback)
    await _bench_server(n=200 if not quick else 50)

    print()


def _bench_rust() -> None:
    from qdap._rust_bridge import backend_info, qft_benchmark
    bi = backend_info()
    label = "Rust" if bi["rust_available"] else "Python"
    n = 100_000
    rate = qft_benchmark(n)
    bar = _ok(f"{rate/1000:.0f}k") if rate > 200_000 else _warn(f"{rate/1000:.0f}k")
    print(f"  {'QFT decisions/s':30s} {bar} decisions/s   ({label} backend)")


def _bench_scheduler(n: int) -> None:
    from qdap.scheduler.qft_scheduler import QFTScheduler
    sched = QFTScheduler()
    t0 = time.perf_counter()
    for i in range(n):
        sched.decide(
            payload_size=1024 * (1 + i % 512),
            rtt_ms=20 + (i % 80),
            loss_rate=0.01 * (i % 15),
        )
    elapsed = time.perf_counter() - t0
    rate = n / elapsed
    bar = _ok(f"{rate/1000:.0f}k") if rate > 50_000 else _warn(f"{rate/1000:.0f}k")
    print(f"  {'QFTScheduler.decide/s':30s} {bar} calls/s")


def _bench_fec() -> None:
    from qdap.transport.fec import AdaptiveFEC, fec_effective_loss
    fec = AdaptiveFEC()
    fec.observe_loss(7, 20)   # 35% loss
    payload = b"emergency data " * 100   # 1.5KB

    t0 = time.perf_counter()
    for _ in range(10_000):
        coded, profile = fec.encode(payload, is_emergency=True)
    elapsed = time.perf_counter() - t0
    rate = 10_000 / elapsed

    eff = fec_effective_loss(0.35, 1, 2)
    improvement = f"{0.35/max(eff,1e-9):.1f}x"
    print(f"  {'AdaptiveFEC.encode/s':30s} {_ok(f'{rate/1000:.0f}k')} calls/s  "
          f"({_ok(improvement)} delivery improvement at 35% loss)")


def _bench_delta() -> None:
    from qdap.compression.delta_encoder import DeltaEncoder
    enc = DeltaEncoder()
    import random
    rng = random.Random(42)
    readings = [{"temp": 23 + rng.gauss(0, 0.1), "co2": 412 + rng.randint(-2, 2),
                 "humidity": 61, "pressure": 1013} for _ in range(1000)]

    total_raw = total_wire = 0
    t0 = time.perf_counter()
    for r in readings:
        raw = len(str(r).encode())
        wire = len(enc.encode(r))
        total_raw += raw
        total_wire += wire
    elapsed = time.perf_counter() - t0

    reduction = (1 - total_wire / max(total_raw, 1)) * 100
    rate = 1000 / elapsed
    print(f"  {'DeltaEncoder IoT stream':30s} {_ok(f'{reduction:.1f}%')} reduction  "
          f"({_ok(f'{rate:.0f}')} frames/s)")


async def _bench_server(n: int) -> None:
    from qdap.server import QDAPServer, QDAPClient

    server = QDAPServer("127.0.0.1", 19990)
    await server.start()
    await asyncio.sleep(0.05)

    payload = b"bench" * 200   # 1KB

    t0 = time.perf_counter()
    try:
        async with QDAPClient("127.0.0.1", 19990) as client:
            for _ in range(n):
                await client.send_multiframe([payload], deadline_ms=[500.0])
    except Exception:
        pass
    elapsed = time.perf_counter() - t0

    mbps = (n * len(payload) * 8) / (elapsed * 1_000_000)
    rate = n / elapsed
    print(f"  {'QDAPClient loopback':30s} {_ok(f'{rate:.0f}')} msg/s  "
          f"({_ok(f'{mbps:.2f}')} Mbps  fire-and-forget)")

    await server.stop()


# ─────────────────────────────────────────────────────────────────────────────
# qdap diagnose
# ─────────────────────────────────────────────────────────────────────────────

def cmd_diagnose() -> None:
    print()
    print(_bold("QDAP environment diagnostics"))
    print()

    # Python version
    import sys
    major, minor = sys.version_info[:2]
    py_ok = major == 3 and minor >= 11
    py_label = f"Python {major}.{minor}"
    print(f"  {'Python version':30s} {_ok(py_label) if py_ok else _err(py_label + ' (need >=3.11)')}")

    # Rust backend
    try:
        from qdap._rust_bridge import backend_info
        bi = backend_info()
        if bi["rust_available"]:
            print(f"  {'Rust backend':30s} {_ok('active (maturin compiled)')}")
        else:
            print(f"  {'Rust backend':30s} {_warn('not compiled — using Python fallback')}")
            print(_dim("    build with: cd qdap_core && maturin develop --release"))
    except Exception as e:
        print(f"  {'Rust backend':30s} {_err(f'error: {e}')}")

    # Core dependencies
    print()
    print("  Core dependencies:")
    _dep_check("numpy",        "numpy",        ">=1.26")
    _dep_check("cryptography", "cryptography", ">=42.0")
    _dep_check("aiohttp",      "aiohttp",      ">=3.9")
    _dep_check("websockets",   "websockets",   ">=12.0")

    print()
    print("  Optional:")
    _dep_check("matplotlib", "matplotlib", ">=3.8",  optional=True)
    _dep_check("scipy",      "scipy",      ">=1.12", optional=True)
    _dep_check("rich",       "rich",       ">=13.0", optional=True)
    _dep_check("qiskit",     "qiskit",     ">=1.0",  optional=True)
    _dep_check("aioquic",    "aioquic",    ">=1.0",  optional=True)

    # Port availability
    print()
    print("  Port check (default QDAP port 9000):")
    _port_check(9000)

    # Quick self-test
    print()
    print("  Self-test:")
    try:
        import qdap
        fec = qdap.AdaptiveFEC()
        fec.observe_loss(3, 10)
        coded, profile = fec.encode(b"test", is_emergency=True)
        assert len(coded) >= 1
        enc = qdap.DeltaEncoder()
        enc.encode({"k": 1})
        enc.encode({"k": 1})
        print(f"  {'FEC + Delta smoke test':30s} {_ok('pass')}")
    except Exception as e:
        print(f"  {'FEC + Delta smoke test':30s} {_err(f'FAIL: {e}')}")

    try:
        from qdap._rust_bridge import hash_frame
        d = hash_frame(b"qdap")
        assert len(d) == 32
        print(f"  {'SHA3-256 bridge':30s} {_ok('pass')}")
    except Exception as e:
        print(f"  {'SHA3-256 bridge':30s} {_err(f'FAIL: {e}')}")

    print()


def _dep_check(label: str, module: str, version_req: str, optional: bool = False) -> None:
    try:
        m = __import__(module)
        ver = getattr(m, "__version__", "?")
        print(f"  {label:20s} {_ok(ver):30s} (need {version_req})")
    except ImportError:
        if optional:
            print(f"  {label:20s} {_dim('not installed'):30s} (optional)")
        else:
            print(f"  {label:20s} {_err('MISSING'):30s} pip install {module}")


def _port_check(port: int) -> None:
    import socket
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", port))
        s.close()
        print(f"  port {port:5d}   {_ok('available')}")
    except OSError:
        print(f"  port {port:5d}   {_warn('in use — choose another port')}")


# ─────────────────────────────────────────────────────────────────────────────
# entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        _usage()
        return

    cmd = args[0]

    if cmd == "info":
        cmd_info()

    elif cmd == "diagnose":
        cmd_diagnose()

    elif cmd == "ping":
        if len(args) < 2:
            print(_err("usage: qdap ping <host> [port] [--count N]"))
            sys.exit(1)
        host = args[1]
        port = 9000
        count = 4
        i = 2
        while i < len(args):
            if args[i] in ("-p", "--port") and i + 1 < len(args):
                port = int(args[i + 1]); i += 2
            elif args[i] in ("-c", "--count") and i + 1 < len(args):
                count = int(args[i + 1]); i += 2
            else:
                try:
                    port = int(args[i]); i += 1
                except ValueError:
                    i += 1
        cmd_ping(host, port, count)

    elif cmd == "bench":
        quick = "--quick" in args or "-q" in args
        cmd_bench(quick)

    else:
        print(_err(f"unknown command: {cmd}"))
        _usage()
        sys.exit(1)


def _usage() -> None:
    print()
    print(_bold("qdap") + " — Quantum-Inspired Dynamic Application Protocol CLI")
    print()
    print("  qdap " + _bold("info") + "                  show version, backend, installed modules")
    print("  qdap " + _bold("ping") + " <host> [port]    send emergency frame, measure RTT")
    print("  qdap " + _bold("bench") + " [--quick]        local throughput + FEC + scheduler bench")
    print("  qdap " + _bold("diagnose") + "               check environment and dependencies")
    print()
    print("  " + _dim("pip install qdap         — core (numpy, cryptography, aiohttp, websockets)"))
    print("  " + _dim("pip install qdap[viz]    — add matplotlib/rich/scipy"))
    print("  " + _dim("pip install qdap[qiskit] — add Qiskit Fourier verification"))
    print()


if __name__ == "__main__":
    main()
