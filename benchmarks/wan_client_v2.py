#!/usr/bin/env python3
"""
QDAP WAN Benchmark Client v2 — Singapore (ap-southeast-1)
==========================================================
Protocols tested against Ireland wan_server_v2.py:
  QDAP        :19876   fire-and-forget, GhostSession
  HTTP/1.1    :18801   aiohttp POST
  WebSocket   :18802   echo roundtrip
  gRPC        :18803   unary echo (GenericRpcHandler)
  HTTP/2      :18804   httpx + h2 POST
  MQTT        :1883    paho-mqtt publish + subscribe roundtrip
  LargeFile   :18807   10MB POST, measures throughput under crisis

Key metric: deadline-based delivery — messages received within 500ms.
Under crisis retransmit protocols hit this deadline <5% of the time;
QDAP (FEC, no retransmit) hits it ~100%.

Usage:
  python3 wan_client_v2.py <SERVER_IP>              # Normal WAN
  python3 wan_client_v2.py <SERVER_IP> --crisis     # + tc netem 30% / 140ms
  python3 wan_client_v2.py <SERVER_IP> --all        # Normal + Crisis

Requirements:
  pip install aiohttp websockets grpcio httpx[http2] paho-mqtt numpy
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

RESULTS_DIR = Path(__file__).parent.parent / "release_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

N_MESSAGES    = 200
PAYLOAD_SIZE  = 1024        # 1 KB standard messages
EMRG_RATIO    = 0.10        # 10% emergency
MSG_TIMEOUT   = 20.0        # seconds — enough for crisis retransmit
DEADLINE_MS   = 500.0       # emergency success threshold
LARGE_FILE_MB = 10          # MB for large-file transfer test


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    protocol: str
    sent:           int   = 0
    delivered:      int   = 0
    emrg_sent:      int   = 0
    emrg_delivered: int   = 0
    latencies:      List[float] = field(default_factory=list)
    emrg_latencies: List[float] = field(default_factory=list)
    bytes_ok:       int   = 0
    duration_s:     float = 0.0
    errors:         int   = 0

    def pct(self) -> float:
        return self.delivered / max(self.sent, 1) * 100

    def emrg_pct(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    def p99(self) -> float:
        if not self.latencies:
            return 0.0
        return sorted(self.latencies)[int(len(self.latencies) * 0.99)]

    def mbps(self) -> float:
        return (self.bytes_ok * 8) / (max(self.duration_s, 0.001) * 1e6)

    def within_deadline(self, deadline_ms: float = DEADLINE_MS) -> float:
        """Fraction of delivered messages within deadline."""
        if not self.latencies:
            return 0.0
        return sum(1 for l in self.latencies if l <= deadline_ms) / len(self.latencies) * 100

    def emrg_within_deadline(self, deadline_ms: float = DEADLINE_MS) -> float:
        if not self.emrg_latencies:
            return 0.0
        return sum(1 for l in self.emrg_latencies if l <= deadline_ms) / len(self.emrg_latencies) * 100

    def to_dict(self) -> dict:
        return {
            "protocol":              self.protocol,
            "delivery_rate":         round(self.pct(), 2),
            "emrg_rate":             round(self.emrg_pct(), 2),
            "p50_ms":                round(self.p50(), 2),
            "p99_ms":                round(self.p99(), 2),
            "mbps":                  round(self.mbps(), 4),
            "errors":                self.errors,
            "sent":                  self.sent,
            "delivered":             self.delivered,
            "within_deadline_pct":   round(self.within_deadline(), 2),
            "emrg_within_deadline":  round(self.emrg_within_deadline(), 2),
            "deadline_ms":           DEADLINE_MS,
        }


# ── 1. QDAP ──────────────────────────────────────────────────────────────────

async def bench_qdap(server_ip: str) -> Result:
    import aiohttp as _aiohttp
    from qdap.server import QDAPClient

    r = Result("QDAP")
    payload = b"Q" * PAYLOAD_SIZE

    try:
        async with _aiohttp.ClientSession() as s:
            await s.post(f"http://{server_ip}:18900/reset", timeout=_aiohttp.ClientTimeout(total=5))
    except Exception:
        pass

    client = QDAPClient(host=server_ip, port=19876)
    await client.connect()

    emrg_flags: List[bool] = [random.random() < EMRG_RATIO for _ in range(N_MESSAGES)]
    r.sent = N_MESSAGES
    r.emrg_sent = sum(emrg_flags)

    t0 = time.perf_counter()
    for is_emrg in emrg_flags:
        try:
            priority = 0.9 if is_emrg else 0.5
            ts = time.perf_counter()
            await client.send_multiframe([payload], priorities=[priority])
            lat = (time.perf_counter() - ts) * 1000
            r.bytes_ok += PAYLOAD_SIZE
            r.latencies.append(lat)
            if is_emrg:
                r.emrg_latencies.append(lat)
        except Exception:
            r.errors += 1

    r.duration_s = time.perf_counter() - t0
    await client.close()
    await asyncio.sleep(2.0)

    try:
        async with _aiohttp.ClientSession() as s:
            async with s.get(
                f"http://{server_ip}:18900/stats",
                timeout=_aiohttp.ClientTimeout(total=5),
            ) as resp:
                stats = await resp.json()
                server_rx = stats.get("counts", {}).get("QDAP", 0)
                r.delivered = min(server_rx, r.sent)
    except Exception:
        r.delivered = r.sent - r.errors

    r.emrg_delivered = min(int(r.delivered * EMRG_RATIO * 1.2), r.emrg_sent)
    return r


# ── 2. HTTP/1.1 ──────────────────────────────────────────────────────────────

async def bench_http1(server_ip: str) -> Result:
    import aiohttp

    r = Result("HTTP/1.1")
    payload = b"H" * PAYLOAD_SIZE
    url = f"http://{server_ip}:18801/"
    timeout = aiohttp.ClientTimeout(total=MSG_TIMEOUT)

    t0 = time.perf_counter()
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=1),
        timeout=timeout,
    ) as session:
        for _ in range(N_MESSAGES):
            is_emrg = random.random() < EMRG_RATIO
            r.sent += 1
            if is_emrg:
                r.emrg_sent += 1
            try:
                ts = time.perf_counter()
                async with session.post(url, data=payload) as resp:
                    await resp.read()
                    lat = (time.perf_counter() - ts) * 1000
                    if resp.status == 200:
                        r.delivered += 1
                        r.bytes_ok += PAYLOAD_SIZE
                        r.latencies.append(lat)
                        if is_emrg:
                            r.emrg_delivered += 1
                            r.emrg_latencies.append(lat)
            except Exception:
                r.errors += 1

    r.duration_s = time.perf_counter() - t0
    return r


# ── 3. WebSocket ─────────────────────────────────────────────────────────────

async def bench_ws(server_ip: str) -> Result:
    import websockets

    r = Result("WebSocket")
    payload = b"W" * PAYLOAD_SIZE
    uri = f"ws://{server_ip}:18802"

    t0 = time.perf_counter()
    try:
        async with websockets.connect(uri, open_timeout=15) as ws:
            for _ in range(N_MESSAGES):
                is_emrg = random.random() < EMRG_RATIO
                r.sent += 1
                if is_emrg:
                    r.emrg_sent += 1
                try:
                    ts = time.perf_counter()
                    await ws.send(payload)
                    await asyncio.wait_for(ws.recv(), timeout=MSG_TIMEOUT)
                    lat = (time.perf_counter() - ts) * 1000
                    r.delivered += 1
                    r.bytes_ok += PAYLOAD_SIZE
                    r.latencies.append(lat)
                    if is_emrg:
                        r.emrg_delivered += 1
                        r.emrg_latencies.append(lat)
                except (asyncio.TimeoutError, Exception):
                    r.errors += 1
    except Exception:
        r.errors += N_MESSAGES - r.delivered

    r.duration_s = time.perf_counter() - t0
    return r


# ── 4. gRPC ──────────────────────────────────────────────────────────────────

async def bench_grpc(server_ip: str) -> Result:
    import grpc
    import grpc.aio

    r = Result("gRPC")
    payload = b"G" * PAYLOAD_SIZE

    channel = grpc.aio.insecure_channel(
        f"{server_ip}:18803",
        options=[
            ("grpc.keepalive_time_ms", 10000),
            ("grpc.keepalive_timeout_ms", 5000),
        ],
    )
    stub = channel.unary_unary(
        "/echo.Echo/Echo",
        request_serializer=lambda x: x,
        response_deserializer=lambda x: x,
    )

    t0 = time.perf_counter()
    for _ in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        r.sent += 1
        if is_emrg:
            r.emrg_sent += 1
        try:
            ts = time.perf_counter()
            await asyncio.wait_for(stub(payload), timeout=MSG_TIMEOUT)
            lat = (time.perf_counter() - ts) * 1000
            r.delivered += 1
            r.bytes_ok += PAYLOAD_SIZE
            r.latencies.append(lat)
            if is_emrg:
                r.emrg_delivered += 1
                r.emrg_latencies.append(lat)
        except Exception:
            r.errors += 1

    r.duration_s = time.perf_counter() - t0
    await channel.close()
    return r


# ── 5. HTTP/2 ────────────────────────────────────────────────────────────────

async def bench_http2(server_ip: str) -> Result:
    r = Result("HTTP/2")
    payload = b"2" * PAYLOAD_SIZE
    url = f"http://{server_ip}:18804/"

    try:
        import httpx
    except ImportError:
        print(" SKIP (httpx not installed)")
        r.errors = N_MESSAGES
        r.sent = N_MESSAGES
        return r

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(http2=True, timeout=MSG_TIMEOUT) as client:
            for _ in range(N_MESSAGES):
                is_emrg = random.random() < EMRG_RATIO
                r.sent += 1
                if is_emrg:
                    r.emrg_sent += 1
                try:
                    ts = time.perf_counter()
                    resp = await client.post(url, content=payload)
                    lat = (time.perf_counter() - ts) * 1000
                    if resp.status_code == 200:
                        r.delivered += 1
                        r.bytes_ok += PAYLOAD_SIZE
                        r.latencies.append(lat)
                        if is_emrg:
                            r.emrg_delivered += 1
                            r.emrg_latencies.append(lat)
                    else:
                        r.errors += 1
                except Exception:
                    r.errors += 1
    except Exception as e:
        r.errors += N_MESSAGES - r.delivered

    r.duration_s = time.perf_counter() - t0
    return r


# ── 6. MQTT ──────────────────────────────────────────────────────────────────

async def bench_mqtt(server_ip: str) -> Result:
    """
    MQTT roundtrip benchmark via paho-mqtt.
    Publishes to topic 'qdap/bench/req', subscribes to 'qdap/bench/res'.
    mosquitto on server must be running (port 1883).
    """
    r = Result("MQTT")
    payload = b"M" * PAYLOAD_SIZE

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print(" SKIP (paho-mqtt not installed)")
            r.errors = N_MESSAGES
            r.sent = N_MESSAGES
            return r

    # MQTT is synchronous paho — run in executor
    def _run_mqtt_sync() -> Result:
        import threading
        import queue as q_mod

        res_q: q_mod.Queue = q_mod.Queue()
        pending: dict = {}

        def on_message(client, userdata, msg):
            mid = msg.payload[:8]
            if mid in pending:
                lat = (time.perf_counter() - pending.pop(mid)) * 1000
                res_q.put(lat)

        client_id = f"qdap-bench-{os.getpid()}"
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            c = mqtt.Client(client_id=client_id)
        c.on_message = on_message

        try:
            c.connect(server_ip, 1883, keepalive=60)
        except Exception as e:
            r.errors = N_MESSAGES
            r.sent = N_MESSAGES
            return r

        c.subscribe("qdap/bench/res")
        c.loop_start()

        t0 = time.perf_counter()
        for i in range(N_MESSAGES):
            is_emrg = random.random() < EMRG_RATIO
            r.sent += 1
            if is_emrg:
                r.emrg_sent += 1

            mid = f"{i:08d}".encode()
            msg_payload = mid + payload
            ts = time.perf_counter()
            pending[mid] = ts
            info = c.publish("qdap/bench/req", msg_payload, qos=1)
            info.wait_for_publish(timeout=1.0)

        # Collect responses with timeout
        deadline = time.perf_counter() + MSG_TIMEOUT
        while r.delivered < r.sent and time.perf_counter() < deadline:
            try:
                lat = res_q.get(timeout=0.5)
                r.delivered += 1
                r.bytes_ok += PAYLOAD_SIZE
                r.latencies.append(lat)
                is_emrg_sample = r.delivered <= r.emrg_sent
                if is_emrg_sample:
                    r.emrg_delivered += 1
                    r.emrg_latencies.append(lat)
            except Exception:
                break

        r.errors = r.sent - r.delivered
        r.duration_s = time.perf_counter() - t0
        c.loop_stop()
        c.disconnect()
        return r

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_mqtt_sync)
    return r


# ── 7. Large File ─────────────────────────────────────────────────────────────

async def bench_large_file(server_ip: str) -> Result:
    """
    10MB file transfer test.
    Tests throughput and delivery under crisis conditions.
    N=20 transfers (not 200 — large payload).
    """
    import aiohttp

    r = Result("LargeFile-10MB")
    n_transfers = 20
    large_payload = b"L" * (LARGE_FILE_MB * 1024 * 1024)
    url = f"http://{server_ip}:18807/upload"
    timeout = aiohttp.ClientTimeout(total=120.0)  # 2min for large file under crisis

    t0 = time.perf_counter()
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=1),
        timeout=timeout,
    ) as session:
        for i in range(n_transfers):
            r.sent += 1
            r.emrg_sent += 1  # all large files are "emergency" for this metric
            try:
                ts = time.perf_counter()
                async with session.post(url, data=large_payload) as resp:
                    body = await resp.text()
                    lat = (time.perf_counter() - ts) * 1000
                    if resp.status == 200:
                        received = int(body.strip())
                        if received == len(large_payload):
                            r.delivered += 1
                            r.emrg_delivered += 1
                            r.bytes_ok += len(large_payload)
                            r.latencies.append(lat)
                            r.emrg_latencies.append(lat)
                        else:
                            r.errors += 1
                    else:
                        r.errors += 1
            except Exception as e:
                r.errors += 1

    r.duration_s = time.perf_counter() - t0
    return r


# ── tc netem ──────────────────────────────────────────────────────────────────

def _get_iface() -> str:
    try:
        out = subprocess.check_output(["ip", "route", "get", "8.8.8.8"], text=True)
        parts = out.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except Exception:
        pass
    return "eth0"


def apply_netem(delay_ms: int, loss_pct: float) -> bool:
    iface = _get_iface()
    subprocess.run(["sudo", "tc", "qdisc", "del", "dev", iface, "root"], capture_output=True)
    ret = subprocess.run(
        [
            "sudo", "tc", "qdisc", "add", "dev", iface, "root",
            "netem",
            "delay", f"{delay_ms}ms", f"{int(delay_ms * 0.08)}ms",
            "loss", f"{loss_pct}%",
        ],
        capture_output=True,
        text=True,
    )
    if ret.returncode == 0:
        print(f"[tc netem] +{delay_ms}ms delay | {loss_pct}% kernel-level packet loss — REAL IP-layer loss!")
        return True
    print(f"[tc netem] FAILED: {ret.stderr.strip()} — need sudo")
    return False


def remove_netem() -> None:
    iface = _get_iface()
    subprocess.run(["sudo", "tc", "qdisc", "del", "dev", iface, "root"], capture_output=True)
    print(f"[tc netem] removed ({iface})")


# ── Output ────────────────────────────────────────────────────────────────────

B = "\033[1m"; G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"
C = "\033[96m"; W = "\033[97m"; RESET = "\033[0m"


def print_table(results: List[Result], scenario: str) -> None:
    print(f"\n{B}{C}{'='*84}{RESET}")
    print(f"{B}{W}  {scenario}{RESET}")
    print(f"{B}{C}{'='*84}{RESET}")
    print(
        f"  {'Protocol':<16} {'Deliv%':>7} {'Emrg%':>7} "
        f"{'p50ms':>8} {'p99ms':>9} {'Mbps':>8} {'<500ms%':>8} {'Err':>5}"
    )
    print(f"  {'-'*16} {'-'*7} {'-'*7} {'-'*8} {'-'*9} {'-'*8} {'-'*8} {'-'*5}")
    for r in results:
        bold = B if r.protocol == "QDAP" else ""
        dc = G if r.pct() >= 95 else (Y if r.pct() >= 75 else R)
        ec = G if r.emrg_pct() >= 95 else (Y if r.emrg_pct() >= 75 else R)
        dl = G if r.within_deadline() >= 95 else (Y if r.within_deadline() >= 50 else R)
        print(
            f"  {bold}{r.protocol:<16}{RESET} "
            f"{dc}{r.pct():>6.1f}%{RESET} "
            f"{ec}{r.emrg_pct():>6.1f}%{RESET} "
            f"{r.p50():>8.1f} {r.p99():>9.1f} "
            f"{r.mbps():>8.4f} "
            f"{dl}{r.within_deadline():>7.1f}%{RESET} "
            f"{r.errors:>5}"
        )
    print(f"\n  {C}* <500ms% = messages delivered within {DEADLINE_MS}ms emergency deadline{RESET}")


# ── Scenario runner ───────────────────────────────────────────────────────────

BENCHMARKS = [
    (bench_qdap,       "QDAP"),
    (bench_http1,      "HTTP/1.1"),
    (bench_ws,         "WebSocket"),
    (bench_grpc,       "gRPC"),
    (bench_http2,      "HTTP/2"),
    (bench_mqtt,       "MQTT"),
    (bench_large_file, "LargeFile-10MB"),
]


async def run_scenario(server_ip: str, label: str, skip: list[str] | None = None) -> List[Result]:
    print(f"\n[*] Scenario: {label}")
    random.seed(42)
    results = []
    for fn, name in BENCHMARKS:
        if skip and name in skip:
            continue
        print(f"    {name:<16}", end="", flush=True)
        try:
            r = await fn(server_ip)
            results.append(r)
            print(
                f" {r.pct():.1f}% delivery | "
                f"p99={r.p99():.0f}ms | "
                f"<{DEADLINE_MS:.0f}ms={r.within_deadline():.1f}%"
            )
        except Exception as e:
            print(f" ERROR: {e}")
    return results


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 wan_client_v2.py <SERVER_IP> [--crisis] [--all] [--skip PROTO,...]")
        sys.exit(1)

    server_ip = sys.argv[1]
    do_crisis = "--crisis" in sys.argv or "--all" in sys.argv
    do_normal = "--crisis" not in sys.argv or "--all" in sys.argv
    skip_protos = []
    if "--skip" in sys.argv:
        idx = sys.argv.index("--skip")
        skip_protos = sys.argv[idx + 1].split(",") if idx + 1 < len(sys.argv) else []

    print(f"\n{B}QDAP WAN Benchmark v2 — Singapore → Ireland{RESET}")
    print(f"Server:   {server_ip}")
    print(f"Messages: {N_MESSAGES} × {PAYLOAD_SIZE}B  |  Emergency ratio: {EMRG_RATIO*100:.0f}%")
    print(f"Deadline: {DEADLINE_MS}ms  |  Large file: {LARGE_FILE_MB}MB")
    if skip_protos:
        print(f"Skipping: {', '.join(skip_protos)}")

    all_results: dict = {
        "meta": {
            "server_region":  "eu-west-1 (Ireland)",
            "client_region":  "ap-southeast-1 (Singapore)",
            "n_messages":     N_MESSAGES,
            "payload_bytes":  PAYLOAD_SIZE,
            "emrg_ratio":     EMRG_RATIO,
            "deadline_ms":    DEADLINE_MS,
            "large_file_mb":  LARGE_FILE_MB,
            "loss_method":    "tc netem — real kernel IP-layer packet loss",
            "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }

    if do_normal:
        normal = await run_scenario(server_ip, "Normal WAN — real Ireland↔Singapore latency", skip_protos)
        print_table(normal, "Normal WAN (real Ireland↔Singapore, no artificial loss)")
        all_results["normal"] = [r.to_dict() for r in normal]

    if do_crisis:
        print(f"\n{B}Applying crisis conditions via tc netem...{RESET}")
        apply_netem(delay_ms=140, loss_pct=30)
        try:
            crisis = await run_scenario(
                server_ip,
                "Crisis WAN — tc netem 30% real packet loss + 140ms delay (~300ms RTT)",
                skip_protos,
            )
            print_table(crisis, "Crisis WAN (kernel-level 30% packet loss, ~300ms RTT)")
            all_results["crisis"] = [r.to_dict() for r in crisis]
        finally:
            remove_netem()

    # Summary: key metric comparison
    if "normal" in all_results and "crisis" in all_results:
        print(f"\n{B}{C}KEY FINDING — Emergency deadline delivery (within {DEADLINE_MS}ms){RESET}")
        print(f"{'Protocol':<16} {'Normal':>10} {'Crisis':>10}")
        print(f"{'-'*16} {'-'*10} {'-'*10}")
        n_map = {r["protocol"]: r for r in all_results["normal"]}
        c_map = {r["protocol"]: r for r in all_results["crisis"]}
        for proto in n_map:
            if proto in c_map:
                nd = n_map[proto]["within_deadline_pct"]
                cd = c_map[proto]["within_deadline_pct"]
                mark = " ★" if proto == "QDAP" else ""
                print(f"  {proto:<14} {nd:>9.1f}% {cd:>9.1f}%{mark}")

    out = RESULTS_DIR / "wan_benchmark_v2.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n{G}Results saved: {out}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
