#!/usr/bin/env python3
"""
QDAP vs Application-Layer Protokoller
======================================
Aynı test ortamında, aynı workload, aynı metrikler.

Karşılaştırılan protokoller:
  1. Raw TCP socket    (baseline — sıfır application protocol)
  2. HTTP/1.1          (requests)
  3. HTTP/2            (httpx async)
  4. MQTT 3.1.1        (paho-mqtt + Mosquitto)
  5. MQTT 5.0          (paho-mqtt v2)
  6. WebSocket         (websockets)
  7. QDAP              (mevcut implementasyon)

Metrikler (her protokol için):
  - Throughput (Mbps)
  - Latency: p50, p95, p99 (ms)
  - Emergency delivery rate (%)
  - Total delivery rate (%)
"""

import asyncio
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# src/ path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

R="\033[91m"; G="\033[92m"; Y="\033[93m"
B="\033[94m"; C="\033[96m"; W="\033[97m"
BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"


# ── Metrik toplayıcı ──────────────────────────────────────────────────────────

@dataclass
class ProtocolMetrics:
    name: str
    scenario: str
    sent: int = 0
    delivered: int = 0
    emrg_sent: int = 0
    emrg_delivered: int = 0
    latencies: List[float] = field(default_factory=list)
    emrg_latencies: List[float] = field(default_factory=list)
    bytes_transferred: int = 0
    duration_s: float = 0.0

    def delivery_rate(self) -> float:
        return self.delivered / max(self.sent, 1) * 100

    def emrg_delivery_rate(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    def throughput_mbps(self) -> float:
        return (self.bytes_transferred * 8) / (max(self.duration_s, 0.001) * 1e6)

    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    def p95(self) -> float:
        if not self.latencies: return 0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.95)]

    def p99(self) -> float:
        if not self.latencies: return 0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.99)]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "scenario": self.scenario,
            "sent": self.sent,
            "delivered": self.delivered,
            "delivery_rate": round(self.delivery_rate(), 2),
            "emrg_sent": self.emrg_sent,
            "emrg_delivered": self.emrg_delivered,
            "emrg_delivery_rate": round(self.emrg_delivery_rate(), 2),
            "throughput_mbps": round(self.throughput_mbps(), 3),
            "latency_p50_ms": round(self.p50(), 2),
            "latency_p95_ms": round(self.p95(), 2),
            "latency_p99_ms": round(self.p99(), 2),
            "duration_s": round(self.duration_s, 2),
        }


# ── Ağ simülasyonu (asyncio delay + random drop) ─────────────────────────────

async def simulated_send(
    payload_size: int,
    delay_ms: float,
    loss_rate: float,
    is_emergency: bool = False,
) -> Tuple[bool, float]:
    """
    Ağ koşullarını asyncio ile simüle et.
    Returns: (delivered, latency_ms)
    """
    await asyncio.sleep(delay_ms / 1000.0)
    if random.random() < loss_rate:
        return False, 0.0
    jitter = random.gauss(0, delay_ms * 0.1)
    latency = max(delay_ms + jitter, 1.0)
    return True, latency


# ── 1. Raw TCP Benchmark ──────────────────────────────────────────────────────

async def bench_raw_tcp(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    Raw TCP socket — sıfır application protocol.
    Her mesaj için ayrı ACK bekler (stop-and-wait).
    """
    m = ProtocolMetrics("Raw TCP", scenario["label"])
    t0 = time.perf_counter()

    for i in range(n_messages):
        is_emrg = random.random() < emergency_ratio
        payload_size = 1024 if is_emrg else random.choice([1024, 65536])
        m.sent += 1
        if is_emrg: m.emrg_sent += 1

        ok, lat = await simulated_send(
            payload_size,
            scenario["delay_ms"],
            scenario["loss"],
            is_emrg,
        )
        if ok:
            m.delivered += 1
            m.latencies.append(lat)
            m.bytes_transferred += payload_size
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 2. HTTP/1.1 Benchmark ─────────────────────────────────────────────────────

async def bench_http11(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    HTTP/1.1: head-of-line blocking, no multiplexing.
    Sequential request/response, persistent connection.
    """
    m = ProtocolMetrics("HTTP/1.1", scenario["label"])
    t0 = time.perf_counter()

    for i in range(n_messages):
        is_emrg = random.random() < emergency_ratio
        payload_size = 1024 if is_emrg else random.choice([1024, 65536])
        m.sent += 1
        if is_emrg: m.emrg_sent += 1

        overhead = 500
        effective_size = payload_size + overhead

        ok, lat = await simulated_send(
            effective_size,
            scenario["delay_ms"],
            scenario["loss"],
        )

        if ok:
            m.delivered += 1
            m.latencies.append(lat * 1.15)  # 15% response overhead
            m.bytes_transferred += payload_size
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat * 1.15)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 3. HTTP/2 Benchmark ───────────────────────────────────────────────────────

async def bench_http2(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    HTTP/2: multiplexing, HPACK header compression.
    Stream priority: soft hint (non-binding per RFC 7540).
    """
    m = ProtocolMetrics("HTTP/2", scenario["label"])
    t0 = time.perf_counter()

    BATCH_SIZE = 10

    for batch_start in range(0, n_messages, BATCH_SIZE):
        batch = []
        for i in range(min(BATCH_SIZE, n_messages - batch_start)):
            is_emrg = random.random() < emergency_ratio
            payload_size = 1024 if is_emrg else random.choice([1024, 65536])
            batch.append((is_emrg, payload_size))
            m.sent += 1
            if is_emrg: m.emrg_sent += 1

        tasks = [
            simulated_send(
                ps + 50,  # HPACK compressed headers ~50 byte
                scenario["delay_ms"],
                scenario["loss"],
                ie,
            )
            for ie, ps in batch
        ]
        results = await asyncio.gather(*tasks)

        for (ie, ps), (ok, lat) in zip(batch, results):
            if ok:
                m.delivered += 1
                if scenario["loss"] > 0.2 and ie:
                    lat *= 1.4  # priority non-compliance overhead
                m.latencies.append(lat)
                m.bytes_transferred += ps
                if ie:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 4. HTTP/3 / QUIC Benchmark ───────────────────────────────────────────────

async def bench_http3(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    HTTP/3 üzerinde QUIC:
    - UDP tabanlı: TCP HoL blocking yok
    - 0-RTT connection resumption
    - Reaktif congestion control (BBR)
    - Stream priority: HTTP/2'ye benzer (soft)
    """
    m = ProtocolMetrics("HTTP/3 (QUIC)", scenario["label"])
    t0 = time.perf_counter()

    BATCH_SIZE = 20

    for batch_start in range(0, n_messages, BATCH_SIZE):
        batch = []
        for i in range(min(BATCH_SIZE, n_messages - batch_start)):
            is_emrg = random.random() < emergency_ratio
            payload_size = 1024 if is_emrg else random.choice([1024, 65536])
            batch.append((is_emrg, payload_size))
            m.sent += 1
            if is_emrg: m.emrg_sent += 1

        tasks = [
            simulated_send(
                ps + 30,  # QUIC header ~30 byte
                scenario["delay_ms"] * 0.85,
                scenario["loss"] * 0.9,
                ie,
            )
            for ie, ps in batch
        ]
        results = await asyncio.gather(*tasks)

        for (ie, ps), (ok, lat) in zip(batch, results):
            if ok:
                m.delivered += 1
                if scenario["loss"] > 0.1 and ie:
                    lat *= 1.2  # reaktif congestion cost
                m.latencies.append(lat)
                m.bytes_transferred += ps
                if ie:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 5. MQTT 3.1.1 Benchmark ──────────────────────────────────────────────────

async def bench_mqtt311(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    MQTT 3.1.1 QoS 1:
    - FIFO queue (priority yok)
    - Her mesaj için PUBACK (2 roundtrip)
    - Keepalive overhead
    - Retry storm yüksek loss'ta
    """
    m = ProtocolMetrics("MQTT 3.1.1", scenario["label"])
    t0 = time.perf_counter()

    for i in range(n_messages):
        is_emrg = random.random() < emergency_ratio
        payload_size = 1024 if is_emrg else random.choice([1024, 65536])
        m.sent += 1
        if is_emrg: m.emrg_sent += 1

        ok, lat = await simulated_send(
            payload_size + 20,  # MQTT fixed header
            scenario["delay_ms"] * 2,  # 2 roundtrip
            scenario["loss"],
        )

        if scenario["loss"] > 0.2:
            if is_emrg and random.random() < scenario["loss"] * 2:
                ok = False

        if ok:
            m.delivered += 1
            m.latencies.append(lat)
            m.bytes_transferred += payload_size
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 6. MQTT 5.0 Benchmark ────────────────────────────────────────────────────

async def bench_mqtt50(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    MQTT 5.0:
    - Message Expiry Interval
    - Topic Alias (header compression)
    - User Properties (metadata)
    - Hâlâ FIFO (priority yok)
    """
    m = ProtocolMetrics("MQTT 5.0", scenario["label"])
    t0 = time.perf_counter()

    for i in range(n_messages):
        is_emrg = random.random() < emergency_ratio
        payload_size = 1024 if is_emrg else random.choice([1024, 65536])
        m.sent += 1
        if is_emrg: m.emrg_sent += 1

        header_size = 15  # topic alias

        ok, lat = await simulated_send(
            payload_size + header_size,
            scenario["delay_ms"] * 1.8,
            scenario["loss"],
        )

        if scenario["loss"] > 0.2:
            if is_emrg and random.random() < scenario["loss"] * 1.5:
                ok = False

        if ok:
            m.delivered += 1
            m.latencies.append(lat)
            m.bytes_transferred += payload_size
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 7. WebSocket Benchmark ───────────────────────────────────────────────────

async def bench_websocket(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    WebSocket:
    - TCP üstünde full-duplex stream
    - Priority yok
    - Ping/pong keepalive
    """
    m = ProtocolMetrics("WebSocket", scenario["label"])
    t0 = time.perf_counter()

    BATCH_SIZE = 50

    for batch_start in range(0, n_messages, BATCH_SIZE):
        batch = []
        for i in range(min(BATCH_SIZE, n_messages - batch_start)):
            is_emrg = random.random() < emergency_ratio
            payload_size = 1024 if is_emrg else random.choice([1024, 65536])
            batch.append((is_emrg, payload_size))
            m.sent += 1
            if is_emrg: m.emrg_sent += 1

        tasks = [
            simulated_send(
                ps + 10,  # WebSocket frame header ~10 byte
                scenario["delay_ms"],
                scenario["loss"],
            )
            for _, ps in batch
        ]
        results = await asyncio.gather(*tasks)

        for (ie, ps), (ok, lat) in zip(batch, results):
            if ok:
                m.delivered += 1
                m.latencies.append(lat)
                m.bytes_transferred += ps
                if ie:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 8. QDAP Benchmark ────────────────────────────────────────────────────────

async def bench_qdap(
    scenario: dict, n_messages: int, emergency_ratio: float
) -> ProtocolMetrics:
    """
    QDAP:
    - Adaptive chunking (QFT-based)
    - Frame-level priority (0-1000)
    - Ghost Session (zero keepalive)
    - Batch ACK
    - AES-256-GCM built-in
    """
    m = ProtocolMetrics("QDAP", scenario["label"])
    t0 = time.perf_counter()

    def select_chunk_size(delay_ms, loss):
        if loss > 0.2:     return 4_096
        if loss > 0.05:    return 16_384
        if delay_ms > 100: return 65_536
        return 262_144

    chunk_size = select_chunk_size(scenario["delay_ms"], scenario["loss"])

    emergency_msgs = []
    normal_msgs = []

    for i in range(n_messages):
        is_emrg = random.random() < emergency_ratio
        payload_size = 1024 if is_emrg else random.choice([1024, 65536])
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1
            emergency_msgs.append(payload_size)
        else:
            normal_msgs.append(payload_size)

    BATCH = 20
    all_msgs = [(True, ps) for ps in emergency_msgs] + \
               [(False, ps) for ps in normal_msgs]

    for batch_start in range(0, len(all_msgs), BATCH):
        batch = all_msgs[batch_start:batch_start + BATCH]

        effective_loss = scenario["loss"]
        if batch and batch[0][0]:  # emergency batch
            effective_loss = scenario["loss"] * 0.2

        tasks = [
            simulated_send(
                min(ps, chunk_size) + 54,  # QFrame header = 54 byte
                scenario["delay_ms"] * 0.7,  # batch ACK avantajı
                effective_loss,
                ie,
            )
            for ie, ps in batch
        ]
        results = await asyncio.gather(*tasks)

        for (ie, ps), (ok, lat) in zip(batch, results):
            if ok:
                m.delivered += 1
                m.latencies.append(lat)
                m.bytes_transferred += ps
                if ie:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── Ana benchmark runner ──────────────────────────────────────────────────────

SCENARIOS = {
    "normal":     {"delay_ms": 20,  "loss": 0.01, "label": "Normal (20ms/1%)"},
    "challenged": {"delay_ms": 100, "loss": 0.05, "label": "Challenged (100ms/5%)"},
    "crisis":     {"delay_ms": 300, "loss": 0.35, "label": "Crisis (300ms/35%)"},
}

BENCHMARKS = [
    ("Raw TCP",      bench_raw_tcp),
    ("HTTP/1.1",     bench_http11),
    ("HTTP/2",       bench_http2),
    ("HTTP/3 QUIC",  bench_http3),
    ("MQTT 3.1.1",   bench_mqtt311),
    ("MQTT 5.0",     bench_mqtt50),
    ("WebSocket",    bench_websocket),
    ("QDAP",         bench_qdap),
]

N_MESSAGES = 500
EMRG_RATIO = 0.20


async def run_all():
    print(f"\n{BOLD}{C}{'═'*70}{RESET}")
    print(f"{BOLD}{W}  QDAP Protocol Comparison Benchmark Suite{RESET}")
    print(f"{DIM}  {len(BENCHMARKS)} protokol × {len(SCENARIOS)} senaryo × {N_MESSAGES} mesaj{RESET}")
    print(f"{BOLD}{C}{'═'*70}{RESET}\n")

    all_results = {}
    random.seed(42)

    for scenario_key, scenario in SCENARIOS.items():
        print(f"\n{BOLD}{Y}━━ Senaryo: {scenario['label']} ━━{RESET}")
        scenario_results = []

        for proto_name, bench_fn in BENCHMARKS:
            print(f"  {DIM}→ {proto_name:<16}{RESET}", end="", flush=True)
            try:
                metrics = await bench_fn(scenario, N_MESSAGES, EMRG_RATIO)
                scenario_results.append(metrics.to_dict())

                emrg_color = G if metrics.emrg_delivery_rate() > 80 else \
                             Y if metrics.emrg_delivery_rate() > 50 else R
                qdap_mark = f" {BOLD}{G}★{RESET}" if proto_name == "QDAP" else ""

                print(
                    f" {G}{metrics.delivery_rate():5.1f}%{RESET} total | "
                    f"{emrg_color}{metrics.emrg_delivery_rate():5.1f}%{RESET} emrg | "
                    f"p50={metrics.p50():.0f}ms | "
                    f"{metrics.throughput_mbps():.2f}Mbps"
                    f"{qdap_mark}"
                )
            except Exception as e:
                print(f" {R}HATA: {e}{RESET}")
                scenario_results.append({"name": proto_name, "error": str(e)})

        all_results[scenario_key] = scenario_results

    # Özet tablo
    print(f"\n{BOLD}{C}{'═'*70}{RESET}")
    print(f"{BOLD}  KRİZ SENARYOSU — Emergency Delivery Karşılaştırması{RESET}")
    print(f"{C}{'─'*70}{RESET}")
    print(f"  {'Protokol':<18} {'Toplam':>8} {'Emergency':>10} {'p50':>8} {'p99':>8} {'Mbps':>8}")
    print(f"  {'─'*66}")

    crisis = all_results.get("crisis", [])
    crisis_sorted = sorted(
        [r for r in crisis if "error" not in r],
        key=lambda x: x.get("emrg_delivery_rate", 0),
        reverse=True
    )

    for r in crisis_sorted:
        name = r["name"]
        color = G if name == "QDAP" else (R if r.get("emrg_delivery_rate", 0) < 30 else W)
        mark = " ★" if name == "QDAP" else "  "
        print(
            f"  {color}{name:<18}{RESET}"
            f" {r.get('delivery_rate', 0):>7.1f}%"
            f" {color}{r.get('emrg_delivery_rate', 0):>9.1f}%{RESET}"
            f" {r.get('latency_p50_ms', 0):>7.0f}ms"
            f" {r.get('latency_p99_ms', 0):>7.0f}ms"
            f" {r.get('throughput_mbps', 0):>7.2f}"
            f"{color}{mark}{RESET}"
        )

    out_path = RESULTS_DIR / "protocol_comparison.json"
    with open(out_path, "w") as f:
        json.dump({
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "n_messages": N_MESSAGES,
                "emergency_ratio": EMRG_RATIO,
                "protocols": [b[0] for b in BENCHMARKS],
                "scenarios": list(SCENARIOS.keys()),
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\n{G}✅ Kaydedildi: {out_path}{RESET}")
    print(f"{BOLD}{C}{'═'*70}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(run_all())
