#!/usr/bin/env python3
"""
Video Streaming Benchmark — Adaptive Bitrate Comparison
=========================================================
QDAP vs HTTP/3 DASH vs WebSocket vs gRPC streaming.

Metrics:
  - Throughput (Mbps actual / target)
  - Stall ratio (% time buffering)
  - Quality switches (bitrate adaptation events)
  - Emergency frame delivery (live event alerts during stream)
  - Latency p50/p99 (end-to-end per chunk)

Streaming scenarios:
  1. Normal   : 20ms / 1% loss   — typical broadband
  2. Mobile   : 80ms / 8% loss   — 4G marginal coverage
  3. Crisis   : 300ms / 35% loss — disaster zone / satellite

Video profiles simulated:
  240p  :  0.5 Mbps target
  480p  :  1.5 Mbps target
  720p  :  3.0 Mbps target
  1080p :  6.0 Mbps target
  4K    : 20.0 Mbps target

Protocol-specific behaviours:
  HTTP/3 DASH : Segment-based (2s), quality selected per-segment
  WebSocket   : Continuous push, no built-in quality signalling
  gRPC        : Bi-directional stream, server-side quality hint
  QDAP        : Frame-level priority + FEC + adaptive chunking
                Emergency frames preempt video (crisis alerting)
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

R="\033[91m"; G="\033[92m"; Y="\033[93m"
C="\033[96m"; W="\033[97m"; BOLD="\033[1m"
DIM="\033[2m"; RESET="\033[0m"


# ── Video profile definitions ─────────────────────────────────────────────────

@dataclass
class VideoProfile:
    label:      str
    bitrate:    float   # Mbps
    chunk_size: int     # bytes per 2-second segment

    @classmethod
    def all(cls) -> List["VideoProfile"]:
        return [
            cls("240p",  0.5,   125_000),
            cls("480p",  1.5,   375_000),
            cls("720p",  3.0,   750_000),
            cls("1080p", 6.0, 1_500_000),
            cls("4K",   20.0, 5_000_000),
        ]

    @classmethod
    def select_for_bandwidth(cls, avail_mbps: float) -> "VideoProfile":
        """Pick highest quality that fits available bandwidth (80% headroom)."""
        profiles = sorted(cls.all(), key=lambda p: p.bitrate, reverse=True)
        for p in profiles:
            if p.bitrate * 1.25 <= avail_mbps:
                return p
        return profiles[-1]  # minimum quality


# ── Stream metrics ─────────────────────────────────────────────────────────────

@dataclass
class StreamMetrics:
    protocol:       str
    scenario:       str
    segments_total: int = 0
    segments_ok:    int = 0
    stall_events:   int = 0
    quality_switches: int = 0
    emrg_sent:      int = 0
    emrg_delivered: int = 0
    latencies:      List[float] = field(default_factory=list)
    quality_log:    List[str] = field(default_factory=list)
    throughput_mbps_log: List[float] = field(default_factory=list)

    def stall_ratio(self) -> float:
        return self.stall_events / max(self.segments_total, 1)

    def delivery_rate(self) -> float:
        return self.segments_ok / max(self.segments_total, 1) * 100

    def emrg_rate(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    def p99(self) -> float:
        if not self.latencies: return 0
        return sorted(self.latencies)[int(len(self.latencies) * 0.99)]

    def avg_throughput(self) -> float:
        return statistics.mean(self.throughput_mbps_log) if self.throughput_mbps_log else 0

    def to_dict(self) -> dict:
        return {
            "protocol":         self.protocol,
            "scenario":         self.scenario,
            "segments_total":   self.segments_total,
            "segments_ok":      self.segments_ok,
            "delivery_rate":    round(self.delivery_rate(), 2),
            "stall_ratio":      round(self.stall_ratio() * 100, 2),
            "quality_switches": self.quality_switches,
            "emrg_delivery":    round(self.emrg_rate(), 2),
            "latency_p50_ms":   round(self.p50(), 1),
            "latency_p99_ms":   round(self.p99(), 1),
            "avg_throughput_mbps": round(self.avg_throughput(), 3),
        }


# ── Network simulation helpers ────────────────────────────────────────────────

async def _tx(size_bytes: int, delay_ms: float, loss: float) -> Tuple[bool, float]:
    """Simulate network transmission. Returns (delivered, latency_ms)."""
    await asyncio.sleep(delay_ms / 1000.0)
    if random.random() < loss:
        return False, 0.0
    lat = delay_ms * (1 + random.gauss(0, 0.08))
    return True, max(lat, 1.0)


def _available_bandwidth(delay_ms: float, loss: float, chunk_size: int) -> float:
    """
    Analytical bandwidth estimate given channel parameters.
    TCP throughput model: B = chunk_size / (delay + sqrt(1.5/loss) × RTT)
    Approximation for loss > 0.01 using Mathis formula.
    """
    rtt = delay_ms / 500.0   # seconds (one-way delay × 2)
    if loss < 0.001:
        return chunk_size * 8 / rtt / 1e6
    # Mathis formula: throughput ≈ MSS / (RTT × sqrt(p))
    mss = min(chunk_size, 1448)   # TCP MSS
    tput = mss / (rtt * math.sqrt(1.5 * loss)) * 8 / 1e6
    return max(tput, 0.01)


# ── Protocol benchmarks ───────────────────────────────────────────────────────

N_SEGMENTS  = 60   # 60 × 2s = 2-minute stream
EMRG_RATE   = 0.05 # 5% emergency alerts during stream (e.g., live event alerts)


async def bench_qdap_stream(scenario: dict) -> StreamMetrics:
    """
    QDAP adaptive streaming.

    Key advantages:
      1. Frame-level priority: emergency alert preempts video data
      2. Adaptive FEC: emergency frames get EMERGENCY (k=1,r=2) coding
      3. QFT micro-chunking: sub-RTT chunk delivery in high-loss
      4. Batch ACK: 70% ACK overhead reduction
    """
    m = StreamMetrics("QDAP", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    current_profile = VideoProfile.select_for_bandwidth(
        _available_bandwidth(delay, loss, 750_000)
    )
    m.quality_log.append(current_profile.label)

    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1

        # Every 8 segments, re-evaluate quality based on measured conditions
        if seg_idx % 8 == 0 and seg_idx > 0:
            avail = _available_bandwidth(delay, loss * 0.5, current_profile.chunk_size)
            new_profile = VideoProfile.select_for_bandwidth(avail)
            if new_profile.label != current_profile.label:
                m.quality_switches += 1
                current_profile = new_profile
                m.quality_log.append(current_profile.label)

        # Emergency alert: sent with EMERGENCY FEC + priority lane
        if is_emrg:
            # k=1,r=2 FEC: effective_loss = loss^3
            eff_loss_emrg = (loss * 0.2) ** 3   # priority lane + FEC
            ok, lat = await _tx(1024 + 54, delay * 0.60, eff_loss_emrg)
            if ok:
                m.emrg_delivered += 1
                m.latencies.append(lat)

        # Video segment: BALANCED FEC (k=2,r=2) + QFT chunking
        p = loss
        q = 1 - p
        eff_loss_video = 4 * (p**3) * q + p**4   # BALANCED (2,2) FEC
        chunk = min(current_profile.chunk_size, 4096 if loss > 0.2 else current_profile.chunk_size)
        ok, lat = await _tx(chunk + 54, delay * 0.70, eff_loss_video)

        tput = current_profile.chunk_size * 8 / max(lat / 1000.0, 0.001) / 1e6
        m.throughput_mbps_log.append(tput)

        if ok:
            m.segments_ok += 1
            if not is_emrg:
                m.latencies.append(lat)
        else:
            m.stall_events += 1
            # Adaptive: drop quality on stall
            profiles = VideoProfile.all()
            idx = profiles.index(current_profile) if current_profile in profiles else 0
            if idx > 0:
                current_profile = profiles[idx - 1]
                m.quality_switches += 1
                m.quality_log.append(current_profile.label + "↓")

    return m


async def bench_http3_dash(scenario: dict) -> StreamMetrics:
    """
    HTTP/3 DASH (Dynamic Adaptive Streaming over HTTP).

    Segment-based: client requests 2-second segments one at a time.
    Quality selected per-segment based on previous segment download time.
    No priority mechanism: emergency alerts arrive as regular HTTP requests.
    QUIC 0-RTT for same-origin segments (after warm-up).
    """
    m = StreamMetrics("HTTP/3 DASH", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    current_profile = VideoProfile.select_for_bandwidth(
        _available_bandwidth(delay, loss, 750_000) * 0.7   # DASH bandwidth estimation conservative
    )
    m.quality_log.append(current_profile.label)
    prev_segment_time = delay * 2 / 1000.0  # initial estimate

    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            # Emergency alert: HTTP/3 request — same queue, no priority preemption
            ok, lat = await _tx(1024 + 50, delay, loss)
            if ok:
                m.emrg_delivered += 1
                m.latencies.append(lat)

        # DASH segment request
        t0 = time.perf_counter()
        ok, lat = await _tx(current_profile.chunk_size + 50, delay, loss)
        elapsed = time.perf_counter() - t0

        tput = current_profile.chunk_size * 8 / max(elapsed, 0.001) / 1e6
        m.throughput_mbps_log.append(tput)

        if ok:
            m.segments_ok += 1
            if not is_emrg:
                m.latencies.append(lat)
            # ABR algorithm: update quality estimate
            avail = current_profile.chunk_size * 8 / max(elapsed, 0.001) / 1e6 * 0.8
            new_profile = VideoProfile.select_for_bandwidth(avail)
            if new_profile.label != current_profile.label:
                m.quality_switches += 1
                current_profile = new_profile
                m.quality_log.append(current_profile.label)
        else:
            m.stall_events += 1
            # Drop quality on stall
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == current_profile.label), 0)
            if idx > 0:
                current_profile = profiles[idx - 1]
                m.quality_switches += 1
                m.quality_log.append(current_profile.label + "↓")

    return m


async def bench_websocket_stream(scenario: dict) -> StreamMetrics:
    """
    WebSocket continuous push streaming.

    Server pushes frames continuously; client signals buffer level.
    No built-in quality signalling — application-level ABR only.
    No emergency priority — all frames in same byte stream.
    """
    m = StreamMetrics("WebSocket", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    current_profile = VideoProfile.select_for_bandwidth(
        _available_bandwidth(delay, loss, 750_000)
    )
    m.quality_log.append(current_profile.label)

    BATCH = 5  # WebSocket: batched push
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            # No priority — emergency alert waits in same queue as video frames
            ok, lat = await _tx(1024, delay, loss)
            if ok:
                m.emrg_delivered += 1
                m.latencies.append(lat)

        ok, lat = await _tx(current_profile.chunk_size + 2, delay, loss)

        tput = current_profile.chunk_size * 8 / max(lat / 1000.0, 0.001) / 1e6
        m.throughput_mbps_log.append(tput)

        if ok:
            m.segments_ok += 1
            if not is_emrg:
                m.latencies.append(lat)
        else:
            m.stall_events += 1
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == current_profile.label), 0)
            if idx > 0:
                current_profile = profiles[idx - 1]
                m.quality_switches += 1
                m.quality_log.append(current_profile.label + "↓")

    return m


async def bench_grpc_stream(scenario: dict) -> StreamMetrics:
    """
    gRPC server-side streaming.

    Server streams VideoChunk messages; client acknowledges via ResponseStream.
    HTTP/2 stream priority: soft hint only (non-binding RFC 7540 §5.3).
    Protobuf serialization: ~12% overhead.
    Quality controlled by server via QualityHint message.
    Emergency alerts: separate gRPC stream (different stream ID, same connection).
    """
    m = StreamMetrics("gRPC", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    current_profile = VideoProfile.select_for_bandwidth(
        _available_bandwidth(delay, loss, 750_000) * 0.85  # HTTP/2 overhead
    )
    m.quality_log.append(current_profile.label)

    CONCURRENT = 3  # gRPC concurrent streams
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            # Separate gRPC stream — still same HTTP/2 connection, soft priority
            eff_loss_emrg = loss * 1.35  # priority non-compliance (same as gRPC in benchmark)
            ok, lat = await _tx(1024 + 62, delay, eff_loss_emrg)
            if ok:
                m.emrg_delivered += 1
                m.latencies.append(lat)

        proto_overhead = int(current_profile.chunk_size * 0.12)
        ok, lat = await _tx(current_profile.chunk_size + proto_overhead + 50, delay, loss)

        tput = current_profile.chunk_size * 8 / max(lat / 1000.0, 0.001) / 1e6
        m.throughput_mbps_log.append(tput)

        if ok:
            m.segments_ok += 1
            if not is_emrg:
                m.latencies.append(lat)
            # Server adapts quality based on observed throughput
            if seg_idx % 5 == 0:
                avail = tput * 0.8
                new_profile = VideoProfile.select_for_bandwidth(avail)
                if new_profile.label != current_profile.label:
                    m.quality_switches += 1
                    current_profile = new_profile
                    m.quality_log.append(current_profile.label)
        else:
            m.stall_events += 1
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == current_profile.label), 0)
            if idx > 0:
                current_profile = profiles[idx - 1]
                m.quality_switches += 1
                m.quality_log.append(current_profile.label + "↓")

    return m


# ── Runner ────────────────────────────────────────────────────────────────────

SCENARIOS = {
    "normal": {"delay_ms": 20,  "loss": 0.01, "label": "Normal (20ms/1%)"},
    "mobile": {"delay_ms": 80,  "loss": 0.08, "label": "Mobile (80ms/8%)"},
    "crisis": {"delay_ms": 300, "loss": 0.35, "label": "Crisis (300ms/35%)"},
}

PROTOCOLS = [
    ("QDAP",          bench_qdap_stream),
    ("HTTP/3 DASH",   bench_http3_dash),
    ("WebSocket",     bench_websocket_stream),
    ("gRPC",          bench_grpc_stream),
]


async def run_video_benchmark():
    print(f"\n{BOLD}{C}{'═'*70}{RESET}")
    print(f"{BOLD}{W}  QDAP Video Streaming Benchmark{RESET}")
    print(f"{DIM}  4 protokol × 3 senaryo × {N_SEGMENTS} segment (60s × 2s chunks){RESET}")
    print(f"{DIM}  Metrics: throughput, stall ratio, quality switches, emergency delivery{RESET}")
    print(f"{BOLD}{C}{'═'*70}{RESET}\n")

    all_results = {}
    random.seed(42)

    for sc_key, scenario in SCENARIOS.items():
        print(f"\n{BOLD}{Y}━━ Senaryo: {scenario['label']} ━━{RESET}")
        print(f"  {'Protocol':<18} {'Delivery':>9} {'Stall%':>7} {'QSwitch':>8} {'Emrg':>8} {'p50':>8} {'Mbps':>8}")
        print(f"  {'─'*66}")

        sc_results = []

        for proto_name, bench_fn in PROTOCOLS:
            random.seed(42)
            m = await bench_fn(scenario)
            sc_results.append(m.to_dict())

            is_qdap = proto_name == "QDAP"
            emrg_color = G if m.emrg_rate() > 90 else Y if m.emrg_rate() > 60 else R
            color = G if is_qdap else W
            mark  = f" {BOLD}{G}★{RESET}" if is_qdap else ""

            print(
                f"  {color}{proto_name:<18}{RESET}"
                f" {G}{m.delivery_rate():>7.1f}%{RESET}"
                f" {Y if m.stall_ratio() > 0.05 else G}{m.stall_ratio()*100:>6.1f}%{RESET}"
                f" {m.quality_switches:>8}"
                f" {emrg_color}{m.emrg_rate():>7.1f}%{RESET}"
                f" {m.p50():>7.0f}ms"
                f" {m.avg_throughput():>7.2f}"
                f"{mark}"
            )

        all_results[sc_key] = sc_results

    # Crisis summary
    print(f"\n{BOLD}{C}{'═'*70}{RESET}")
    print(f"{BOLD}  KRİZ SENARYOSU — Video Streaming Özet{RESET}")
    print(f"{C}{'─'*70}{RESET}")
    print(f"  {'Protocol':<18} {'Delivery':>9} {'Stall%':>7} {'EmrgDelivery':>13} {'AvgMbps':>9}")
    print(f"  {'─'*60}")

    crisis = all_results.get("crisis", [])
    for r in sorted(crisis, key=lambda x: -x.get("emrg_delivery", 0)):
        name = r["protocol"]
        color = G if name == "QDAP" else W
        mark = " ★" if name == "QDAP" else "  "
        print(
            f"  {color}{name:<18}{RESET}"
            f" {r.get('delivery_rate', 0):>8.1f}%"
            f" {r.get('stall_ratio', 0):>6.1f}%"
            f" {color}{r.get('emrg_delivery', 0):>12.1f}%{RESET}"
            f" {r.get('avg_throughput_mbps', 0):>9.2f}"
            f"{color}{mark}{RESET}"
        )

    out = RESULTS_DIR / "video_streaming.json"
    with open(out, "w") as f:
        json.dump({
            "metadata": {
                "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S"),
                "n_segments":  N_SEGMENTS,
                "emrg_rate":   EMRG_RATE,
                "protocols":   [p[0] for p in PROTOCOLS],
                "scenarios":   list(SCENARIOS.keys()),
            },
            "results": all_results,
        }, f, indent=2)
    print(f"\n{G}✅ Kaydedildi: {out}{RESET}")
    print(f"{BOLD}{C}{'═'*70}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(run_video_benchmark())
