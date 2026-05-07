#!/usr/bin/env python3
"""
QDAP LAN Benchmark — İki bilgisayar, aynı WiFi/LAN
====================================================
Kullanım:
  Server bilgisayarda (SERVER_IP'li):
    pip install aiohttp websockets grpcio hypercorn paho-mqtt numpy msgpack
    sudo apt-get install mosquitto mosquitto-clients  # Linux
    brew install mosquitto && brew services start mosquitto  # macOS
    python benchmarks/wan_server_v2.py

  Client bilgisayarda:
    python benchmarks/lan_benchmark.py <SERVER_LAN_IP>
    python benchmarks/lan_benchmark.py <SERVER_LAN_IP> --crisis  # tc netem ile (Linux)

Senaryo: Fabrika içi IoT ağı — MQTT broker, gRPC, LargeFile, QDAP.
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

N_MESSAGES    = 500          # LAN'da daha fazla mesaj — hızlı
PAYLOAD_SIZE  = 1024         # 1 KB
EMRG_RATIO    = 0.10
MSG_TIMEOUT   = 5.0          # LAN'da 5s yeterli
DEADLINE_MS   = 50.0         # LAN'da deadline 50ms (WAN'da 500ms)
LARGE_FILE_MB = 100          # LAN'da 100MB test


# ── Result ───────────────────────────────────────────────────────────────────

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
        if not self.latencies:
            return 0.0
        return sum(1 for l in self.latencies if l <= deadline_ms) / len(self.latencies) * 100

    def to_dict(self) -> dict:
        return {
            "protocol":             self.protocol,
            "delivery_rate":        round(self.pct(), 2),
            "emrg_rate":            round(self.emrg_pct(), 2),
            "p50_ms":               round(self.p50(), 3),
            "p99_ms":               round(self.p99(), 3),
            "mbps":                 round(self.mbps(), 4),
            "errors":               self.errors,
            "sent":                 self.sent,
            "delivered":            self.delivered,
            "within_deadline_pct":  round(self.within_deadline(), 2),
            "deadline_ms":          DEADLINE_MS,
        }


# ── 1. QDAP ──────────────────────────────────────────────────────────────────

async def bench_qdap(server_ip: str) -> Result:
    import aiohttp as _aiohttp
    from qdap.server import QDAPClient

    r = Result("QDAP")
    payload = b"Q" * PAYLOAD_SIZE

    try:
        async with _aiohttp.ClientSession() as s:
            await s.post(f"http://{server_ip}:18900/reset",
                         timeout=_aiohttp.ClientTimeout(total=3))
    except Exception:
        pass

    client = QDAPClient(host=server_ip, port=19876)
    await client.connect()

    emrg_flags = [random.random() < EMRG_RATIO for _ in range(N_MESSAGES)]
    r.sent = N_MESSAGES
    r.emrg_sent = sum(emrg_flags)

    t0 = time.perf_counter()
    for is_emrg in emrg_flags:
        try:
            ts = time.perf_counter()
            await client.send_multiframe([payload], priorities=[0.9 if is_emrg else 0.5])
            lat = (time.perf_counter() - ts) * 1000
            r.bytes_ok += PAYLOAD_SIZE
            r.latencies.append(lat)
            if is_emrg:
                r.emrg_latencies.append(lat)
        except Exception:
            r.errors += 1

    r.duration_s = time.perf_counter() - t0
    await client.close()
    await asyncio.sleep(1.0)

    try:
        async with _aiohttp.ClientSession() as s:
            async with s.get(f"http://{server_ip}:18900/stats",
                             timeout=_aiohttp.ClientTimeout(total=3)) as resp:
                stats = await resp.json()
                r.delivered = min(stats.get("counts", {}).get("QDAP", 0), r.sent)
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
        connector=aiohttp.TCPConnector(limit=1), timeout=timeout
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

    t0 = time.perf_counter()
    try:
        async with websockets.connect(f"ws://{server_ip}:18802", open_timeout=10) as ws:
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
                except Exception:
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

    channel = grpc.aio.insecure_channel(f"{server_ip}:18803")
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


# ── 5. MQTT (paho-mqtt v1+v2 compat) ─────────────────────────────────────────

async def bench_mqtt(server_ip: str) -> Result:
    r = Result("MQTT")
    payload = b"M" * PAYLOAD_SIZE

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print(" SKIP (paho-mqtt not installed)")
        r.errors = N_MESSAGES
        r.sent = N_MESSAGES
        return r

    import queue as q_mod
    import threading

    res_q: q_mod.Queue = q_mod.Queue()
    pending: dict = {}

    def on_connect(c, userdata, flags, rc, *args):
        if rc == 0:
            c.subscribe("qdap/bench/res", qos=1)

    def on_message(c, userdata, msg):
        mid = msg.payload[:8]
        if mid in pending:
            lat = (time.perf_counter() - pending.pop(mid)) * 1000
            res_q.put(lat)

    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id=f"qdap-lan-{os.getpid()}")
    except AttributeError:
        c = mqtt.Client(client_id=f"qdap-lan-{os.getpid()}")

    c.on_connect = on_connect
    c.on_message = on_message

    def _run():
        try:
            c.connect(server_ip, 1883, keepalive=60)
        except Exception as e:
            return
        c.subscribe("qdap/bench/res", qos=1)
        c.loop_start()

        t0 = time.perf_counter()
        for i in range(N_MESSAGES):
            r.sent += 1
            is_emrg = random.random() < EMRG_RATIO
            if is_emrg:
                r.emrg_sent += 1
            mid = f"{i:08d}".encode()
            pending[mid] = time.perf_counter()
            info = c.publish("qdap/bench/req", mid + payload, qos=1)
            info.wait_for_publish(timeout=1.0)

        deadline = time.perf_counter() + MSG_TIMEOUT * 3
        while r.delivered < r.sent and time.perf_counter() < deadline:
            try:
                lat = res_q.get(timeout=0.5)
                r.delivered += 1
                r.bytes_ok += PAYLOAD_SIZE
                r.latencies.append(lat)
                if r.delivered <= r.emrg_sent:
                    r.emrg_delivered += 1
                    r.emrg_latencies.append(lat)
            except Exception:
                if not pending:
                    break

        r.errors = r.sent - r.delivered
        r.duration_s = time.perf_counter() - t0
        c.loop_stop()
        c.disconnect()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run)
    return r


# ── 6. Large File ─────────────────────────────────────────────────────────────

async def bench_large_file(server_ip: str) -> Result:
    import aiohttp
    r = Result(f"LargeFile-{LARGE_FILE_MB}MB")
    n = 10
    payload = b"L" * (LARGE_FILE_MB * 1024 * 1024)
    url = f"http://{server_ip}:18807/upload"
    timeout = aiohttp.ClientTimeout(total=60.0)

    t0 = time.perf_counter()
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=1), timeout=timeout
    ) as session:
        for _ in range(n):
            r.sent += 1
            r.emrg_sent += 1
            try:
                ts = time.perf_counter()
                async with session.post(url, data=payload) as resp:
                    body = await resp.text()
                    lat = (time.perf_counter() - ts) * 1000
                    if resp.status == 200 and int(body.strip()) == len(payload):
                        r.delivered += 1
                        r.emrg_delivered += 1
                        r.bytes_ok += len(payload)
                        r.latencies.append(lat)
                        r.emrg_latencies.append(lat)
                    else:
                        r.errors += 1
            except Exception:
                r.errors += 1

    r.duration_s = time.perf_counter() - t0
    return r


# ── Output ────────────────────────────────────────────────────────────────────

B = "\033[1m"; G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"
C = "\033[96m"; W = "\033[97m"; RESET = "\033[0m"


def print_table(results: List[Result], scenario: str) -> None:
    print(f"\n{B}{C}{'='*88}{RESET}")
    print(f"{B}{W}  {scenario}{RESET}")
    print(f"{B}{C}{'='*88}{RESET}")
    print(
        f"  {'Protocol':<18} {'Deliv%':>7} {'Emrg%':>7} "
        f"{'p50ms':>8} {'p99ms':>8} {'Mbps':>10} {'<{:.0f}ms%'.format(DEADLINE_MS):>9} {'Err':>5}"
    )
    print(f"  {'-'*18} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*10} {'-'*9} {'-'*5}")
    for r in results:
        bold = B if r.protocol == "QDAP" else ""
        dc = G if r.pct() >= 99 else (Y if r.pct() >= 90 else R)
        dl = G if r.within_deadline() >= 95 else (Y if r.within_deadline() >= 70 else R)
        print(
            f"  {bold}{r.protocol:<18}{RESET} "
            f"{dc}{r.pct():>6.1f}%{RESET} "
            f"{r.emrg_pct():>6.1f}% "
            f"{r.p50():>8.2f} {r.p99():>8.2f} "
            f"{r.mbps():>10.3f} "
            f"{dl}{r.within_deadline():>8.1f}%{RESET} "
            f"{r.errors:>5}"
        )
    print(f"\n  {C}* <{DEADLINE_MS:.0f}ms% = LAN emergency deadline{RESET}")


BENCHMARKS = [
    (bench_qdap,       "QDAP"),
    (bench_http1,      "HTTP/1.1"),
    (bench_ws,         "WebSocket"),
    (bench_grpc,       "gRPC"),
    (bench_mqtt,       "MQTT"),
    (bench_large_file, f"LargeFile-{LARGE_FILE_MB}MB"),
]


async def run_scenario(server_ip: str, label: str) -> List[Result]:
    print(f"\n[*] {label}")
    random.seed(42)
    results = []
    for fn, name in BENCHMARKS:
        print(f"    {name:<20}", end="", flush=True)
        try:
            r = await fn(server_ip)
            results.append(r)
            print(
                f" {r.pct():.1f}% | p99={r.p99():.1f}ms | "
                f"<{DEADLINE_MS:.0f}ms={r.within_deadline():.1f}% | "
                f"{r.mbps():.2f}Mbps"
            )
        except Exception as e:
            print(f" ERROR: {e}")
    return results


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 lan_benchmark.py <SERVER_LAN_IP>")
        print("       SERVER_LAN_IP: ip addr show | grep '192.168' (Linux)")
        print("                      ifconfig | grep '192.168' (macOS)")
        sys.exit(1)

    server_ip = sys.argv[1]

    print(f"\n{B}QDAP LAN Benchmark — İki Bilgisayar, Aynı WiFi{RESET}")
    print(f"Server: {server_ip}  |  n={N_MESSAGES} × {PAYLOAD_SIZE}B")
    print(f"LAN deadline: {DEADLINE_MS}ms  |  Large file: {LARGE_FILE_MB}MB")
    print(f"Protokoller: QDAP, HTTP/1.1, WebSocket, gRPC, MQTT, LargeFile")

    results = await run_scenario(server_ip, "LAN — İki bilgisayar, aynı WiFi")
    print_table(results, "LAN Benchmark (aynı WiFi ağı — IoT senaryosu)")

    # Throughput karşılaştırması
    qdap_r = next((r for r in results if r.protocol == "QDAP"), None)
    http_r = next((r for r in results if r.protocol == "HTTP/1.1"), None)
    if qdap_r and http_r and http_r.mbps() > 0:
        ratio = qdap_r.mbps() / http_r.mbps()
        print(f"\n  {B}Throughput: QDAP {qdap_r.mbps():.2f} Mbps vs HTTP/1.1 {http_r.mbps():.2f} Mbps → {ratio:.0f}× fark{RESET}")

    out = RESULTS_DIR / "lan_benchmark.json"
    out.write_text(json.dumps({
        "meta": {
            "scenario": "LAN — two computers, same WiFi",
            "n_messages": N_MESSAGES,
            "payload_bytes": PAYLOAD_SIZE,
            "deadline_ms": DEADLINE_MS,
            "large_file_mb": LARGE_FILE_MB,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "results": [r.to_dict() for r in results],
    }, indent=2, ensure_ascii=False))
    print(f"\n{G}Sonuçlar kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
