#!/usr/bin/env python3
"""
Real Server Benchmark
======================
Gerçek nginx (HTTP/2), apache (HTTP/1.1),
Mosquitto (MQTT) ile QDAP karşılaştırması.

Çalıştırmadan önce:
  bash tests/real_servers/gen_certs.sh
  docker compose -f docker-compose.real-servers.yml up -d
  # 10 saniye bekle
  python benchmarks/real_server_benchmark.py
  docker compose -f docker-compose.real-servers.yml down
"""

import asyncio
import json
import socket
import ssl
import statistics
import sys
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

G="\033[92m"; Y="\033[93m"; C="\033[96m"
W="\033[97m"; BOLD="\033[1m"; RESET="\033[0m"

N_REQUESTS = 200
PAYLOAD_SIZES = [1024, 65536]  # 1KB, 64KB


def check_server(host: str, port: int, timeout: float = 2.0) -> bool:
    """Server ayakta mı kontrol et."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


# ── HTTP/1.1 Benchmark (Apache) ───────────────────────────────────────────────

async def bench_http11_real(n: int, payload_size: int) -> dict:
    """Gerçek Apache HTTP/1.1 server benchmark."""
    if not check_server("localhost", 8081):
        return {"skipped": True, "reason": "Apache not running (port 8081)"}

    latencies = []
    delivered = 0
    t0 = time.perf_counter()

    for _ in range(n):
        try:
            start = time.perf_counter()
            reader, writer = await asyncio.open_connection("localhost", 8081)
            request = (
                f"GET / HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Connection: keep-alive\r\n"
                f"\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            writer.close()
            lat = (time.perf_counter() - start) * 1000
            if b"200" in response or b"HTTP" in response:
                delivered += 1
                latencies.append(lat)
        except Exception:
            pass

    duration = time.perf_counter() - t0
    return {
        "protocol":        "HTTP/1.1 (Apache real)",
        "n_requests":      n,
        "delivered":       delivered,
        "delivery_rate":   round(delivered / n * 100, 2),
        "latency_p50":     round(statistics.median(latencies), 2) if latencies else 0,
        "latency_p95":     round(sorted(latencies)[int(len(latencies)*0.95)], 2) if latencies else 0,
        "throughput_mbps": round((delivered * payload_size * 8) / (duration * 1e6), 3),
        "duration_s":      round(duration, 2),
    }


# ── HTTP/2 Benchmark (nginx) ──────────────────────────────────────────────────

async def bench_http2_real(n: int, payload_size: int) -> dict:
    """Gerçek nginx HTTP/2 benchmark."""
    if not check_server("localhost", 8443):
        return {"skipped": True, "reason": "nginx not running (port 8443)"}

    try:
        import httpx
    except ImportError:
        return {"skipped": True, "reason": "httpx not installed: pip install httpx[http2]"}

    latencies = []
    delivered = 0
    t0 = time.perf_counter()

    async with httpx.AsyncClient(
        base_url="https://localhost:8443",
        http2=True,
        verify=False,  # self-signed cert
        timeout=10.0,
    ) as client:
        for _ in range(n):
            try:
                start = time.perf_counter()
                resp = await client.get("/")
                lat = (time.perf_counter() - start) * 1000
                if resp.status_code == 200:
                    delivered += 1
                    latencies.append(lat)
            except Exception:
                pass

    duration = time.perf_counter() - t0
    return {
        "protocol":        "HTTP/2 (nginx real)",
        "n_requests":      n,
        "delivered":       delivered,
        "delivery_rate":   round(delivered / n * 100, 2),
        "latency_p50":     round(statistics.median(latencies), 2) if latencies else 0,
        "latency_p95":     round(sorted(latencies)[int(len(latencies)*0.95)], 2) if latencies else 0,
        "throughput_mbps": round((delivered * payload_size * 8) / (max(duration, 0.001) * 1e6), 3),
        "duration_s":      round(duration, 2),
    }


# ── MQTT Benchmark (Mosquitto) ────────────────────────────────────────────────

async def bench_mqtt_real(n: int, payload_size: int) -> dict:
    """Gerçek Mosquitto MQTT benchmark."""
    if not check_server("localhost", 1884):
        return {"skipped": True, "reason": "Mosquitto not running (port 1884)"}

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {"skipped": True, "reason": "paho-mqtt not installed"}

    delivered_count = [0]
    latencies = []
    t0 = time.perf_counter()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    msg_times = {}

    def on_message(c, u, msg):
        mid = msg.topic.split("/")[-1]
        if mid in msg_times:
            lat = (time.time() - msg_times[mid]) * 1000
            latencies.append(lat)
            delivered_count[0] += 1

    client.on_message = on_message
    client.connect("localhost", 1884, 60)
    client.subscribe("qdap/bench/+")
    client.loop_start()

    payload = b"x" * min(payload_size, 65535)
    for i in range(n):
        mid = str(i)
        msg_times[mid] = time.time()
        client.publish(f"qdap/bench/{mid}", payload, qos=1)
        await asyncio.sleep(0.005)  # 5ms aralık

    await asyncio.sleep(2.0)  # mesajların gelmesini bekle
    client.loop_stop()
    client.disconnect()

    duration = time.perf_counter() - t0
    d = delivered_count[0]
    return {
        "protocol":        "MQTT 3.1.1 (Mosquitto real)",
        "n_requests":      n,
        "delivered":       d,
        "delivery_rate":   round(d / n * 100, 2),
        "latency_p50":     round(statistics.median(latencies), 2) if latencies else 0,
        "latency_p95":     round(sorted(latencies)[int(len(latencies)*0.95)], 2) if latencies else 0,
        "throughput_mbps": round((d * payload_size * 8) / (max(duration, 0.001) * 1e6), 3),
        "duration_s":      round(duration, 2),
    }


# ── QDAP Benchmark (real server) ─────────────────────────────────────────────

async def bench_qdap_real(n: int, payload_size: int) -> dict:
    """Gerçek QDAP broker benchmark."""
    if not check_server("localhost", 19999):
        return {"skipped": True, "reason": "QDAP server not running (port 19999)"}

    latencies = []
    delivered = 0
    t0 = time.perf_counter()

    for _ in range(n):
        try:
            start = time.perf_counter()
            reader, writer = await asyncio.open_connection("localhost", 19999)
            payload = b"\x51\x44\x41\x50" + b"\x00" * 50 + b"x" * min(payload_size, 4096)
            writer.write(payload)
            await writer.drain()
            resp = await asyncio.wait_for(reader.read(256), timeout=5.0)
            writer.close()
            lat = (time.perf_counter() - start) * 1000
            if len(resp) > 0:
                delivered += 1
                latencies.append(lat)
        except Exception:
            pass

    duration = time.perf_counter() - t0
    return {
        "protocol":        "QDAP (real broker)",
        "n_requests":      n,
        "delivered":       delivered,
        "delivery_rate":   round(delivered / n * 100, 2),
        "latency_p50":     round(statistics.median(latencies), 2) if latencies else 0,
        "latency_p95":     round(sorted(latencies)[int(len(latencies)*0.95)], 2) if latencies else 0,
        "throughput_mbps": round((delivered * payload_size * 8) / (max(duration, 0.001) * 1e6), 3),
        "duration_s":      round(duration, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}{C}{'═'*60}{RESET}")
    print(f"{BOLD}{W}  Real Server Benchmark{RESET}")
    print(f"{BOLD}{C}{'═'*60}{RESET}\n")

    all_results = {}

    for ps_label, ps in [("1KB", 1024), ("64KB", 65536)]:
        print(f"{Y}━━ Payload: {ps_label} ━━{RESET}")
        results = []

        for name, fn in [
            ("HTTP/1.1", bench_http11_real),
            ("HTTP/2",   bench_http2_real),
            ("MQTT",     bench_mqtt_real),
            ("QDAP",     bench_qdap_real),
        ]:
            print(f"  {name}...", end="", flush=True)
            r = await fn(N_REQUESTS, ps)
            results.append(r)

            if r.get("skipped"):
                print(f" {Y}SKIPPED: {r['reason']}{RESET}")
            else:
                mark = f" {G}★{RESET}" if name == "QDAP" else ""
                print(
                    f" {r['delivery_rate']:.1f}% | "
                    f"p50={r['latency_p50']:.0f}ms | "
                    f"{r['throughput_mbps']:.2f}Mbps"
                    f"{mark}"
                )

        all_results[ps_label] = results

    out = RESULTS_DIR / "real_server_benchmark.json"
    with open(out, "w") as f:
        json.dump({
            "metadata": {
                "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
                "n_requests": N_REQUESTS,
                "note":       "Real servers via Docker. Skipped if not running.",
            },
            "results": all_results,
        }, f, indent=2)
    print(f"\n{G}✅ Kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
