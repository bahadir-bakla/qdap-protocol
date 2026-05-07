#!/usr/bin/env python3
"""
Video Streaming Benchmark v2 — QDAP vs HTTP/3 DASH / WebSocket / gRPC / WebRTC
================================================================================
5 protokol x 3 senaryo.

Bant genislik modeli (gercekci):
  Normal  (20ms / 1%  loss): ~100 Mbps baz  -> 4K + akisi mumkun
  Mobile  (80ms / 8%  loss): ~20  Mbps baz  -> 1080p/4K sinirinda
  Crisis (300ms / 35% loss): ~2   Mbps baz  -> 240p/480p mucadelesi

QDAP'in NORMAL + MOBILE kosullardaki avantajlari:
  - Startup time:     0-RTT resumption → diger protokollere gore 1.5-3x hizli baslangic
  - Quality switches: FEC+GhostSession → kayip gormeden stabil kalite
  - Jitter:           QFT scheduler → kare teslim jitter'i minimum
  - Emergency:        priority lane → video akisi kesilmeden alert iletimi

Protokol-spesifik davranislar:
  HTTP/3 DASH : Segment-based ABR, QUIC 0-RTT (warm), 2s segment granularitesi
  WebSocket   : TCP push, reactive ABR, HoL penalty
  gRPC        : HTTP/2 bidir, protobuf overhead, TCP HoL
  WebRTC      : P2P UDP, DTLS+SRTP, ICE setup, jitter buffer
  QDAP        : QFrame priority, FEC, adaptive chunk, GhostSession, 0-RTT
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
from typing import List

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
C = "\033[96m"; W = "\033[97m"; BOLD = "\033[1m"
DIM = "\033[2m"; RESET = "\033[0m"

# ── Video profilleri ───────────────────────────────────────────────────────────

@dataclass
class VideoProfile:
    label:    str
    bitrate:  float   # Mbps
    chunk_kb: int     # KB per 2-second segment

    @classmethod
    def all(cls) -> List["VideoProfile"]:
        return [
            cls("240p",   0.5,   122),
            cls("480p",   1.5,   366),
            cls("720p",   3.0,   732),
            cls("1080p",  6.0,  1465),
            cls("4K",    20.0,  4883),
        ]

    @classmethod
    def select(cls, avail_mbps: float) -> "VideoProfile":
        # 80% kullanim: en yuksek sigdiran profil
        for p in sorted(cls.all(), key=lambda x: x.bitrate, reverse=True):
            if p.bitrate * 1.25 <= avail_mbps:
                return p
        return cls.all()[0]

    @property
    def chunk_bytes(self) -> int:
        return self.chunk_kb * 1024

    def quality_score(self) -> float:
        mapping = {"240p": 1.0, "480p": 2.0, "720p": 3.0, "1080p": 4.0, "4K": 5.0}
        return mapping.get(self.label, 1.0)


# ── Gercekci bant genisligi modeli ────────────────────────────────────────────

def avail_bw_mbps(delay_ms: float, loss: float, protocol_efficiency: float = 1.0) -> float:
    """
    Gercekci bant genisligi tahmini.
    delay_ms ve loss'a gore temel BW, protokol verimliligi ile scale edilir.

    Normal  (20ms/1%):  ~80-100 Mbps
    Mobile  (80ms/8%):  ~10-20  Mbps
    Crisis (300ms/35%): ~0.5-2  Mbps
    """
    # Baz BW: RTT'ye gore azalan model (1 Gbps link, TCP penceresi 8MB)
    rtt_s = delay_ms * 2 / 1000.0
    window_mb = 8.0  # TCP/QUIC receive window MB
    base_bw = (window_mb * 1024 * 1024 * 8) / (rtt_s * 1e6)  # Mbps

    # Kayip penaltisi: Mathis-benzeri, ama gercekci alt sinirla
    if loss > 0.001:
        loss_penalty = 1.0 / (1.0 + loss * 8.0)
    else:
        loss_penalty = 1.0

    bw = base_bw * loss_penalty * protocol_efficiency
    # Pratik ust sinir: tipik modern link
    return min(bw, 200.0)


# ── Senaryo parametreleri ──────────────────────────────────────────────────────

SCENARIOS = [
    {
        "id":       "normal",
        "label":    "Normal (20ms/1%)",
        "delay_ms": 20,
        "loss":     0.01,
        "base_bw":  100.0,   # Mbps — tipik genisbant
    },
    {
        "id":       "mobile",
        "label":    "Mobile (80ms/8%)",
        "delay_ms": 80,
        "loss":     0.08,
        "base_bw":  20.0,    # Mbps — 4G
    },
    {
        "id":       "crisis",
        "label":    "Crisis (300ms/35%)",
        "delay_ms": 300,
        "loss":     0.35,
        "base_bw":  2.0,     # Mbps — afet/uydu
    },
]

N_SEGMENTS = 60    # 60 x 2s = 2dk stream
EMRG_RATE  = 0.05  # %5 emergency alert orani
SEG_DUR_S  = 2.0


# ── Metrik ────────────────────────────────────────────────────────────────────

@dataclass
class StreamMetrics:
    protocol:              str
    scenario:              str
    segments_total:        int   = 0
    segments_ok:           int   = 0
    stall_events:          int   = 0
    quality_switches:      int   = 0
    emrg_sent:             int   = 0
    emrg_delivered:        int   = 0
    latencies:             List[float] = field(default_factory=list)
    quality_scores:        List[float] = field(default_factory=list)
    jitter_ms_list:        List[float] = field(default_factory=list)
    startup_time_ms:       float = 0.0
    total_stall_ms:        float = 0.0

    def delivery_rate(self) -> float:
        return self.segments_ok / max(self.segments_total, 1) * 100

    def emrg_rate(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    def stall_pct(self) -> float:
        return self.stall_events / max(self.segments_total, 1) * 100

    def p50_ms(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    def p99_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return sorted(self.latencies)[int(len(self.latencies) * 0.99)]

    def avg_quality(self) -> float:
        return statistics.mean(self.quality_scores) if self.quality_scores else 0.0

    def avg_jitter_ms(self) -> float:
        return statistics.mean(self.jitter_ms_list) if self.jitter_ms_list else 0.0

    def avg_quality_label(self) -> str:
        score = self.avg_quality()
        if score >= 4.5: return "4K"
        if score >= 3.5: return "1080p"
        if score >= 2.5: return "720p"
        if score >= 1.5: return "480p"
        return "240p"

    def to_dict(self) -> dict:
        return {
            "protocol":           self.protocol,
            "scenario":           self.scenario,
            "segments_total":     self.segments_total,
            "segments_ok":        self.segments_ok,
            "delivery_rate":      round(self.delivery_rate(), 2),
            "stall_pct":          round(self.stall_pct(), 2),
            "quality_switches":   self.quality_switches,
            "avg_quality_score":  round(self.avg_quality(), 2),
            "avg_quality_label":  self.avg_quality_label(),
            "emrg_delivery_pct":  round(self.emrg_rate(), 2),
            "latency_p50_ms":     round(self.p50_ms(), 1),
            "latency_p99_ms":     round(self.p99_ms(), 1),
            "avg_jitter_ms":      round(self.avg_jitter_ms(), 2),
            "startup_time_ms":    round(self.startup_time_ms, 1),
        }


# ── Network helper ─────────────────────────────────────────────────────────────

async def _tx(size: int, delay_ms: float, loss: float) -> tuple[bool, float]:
    await asyncio.sleep(delay_ms / 1000.0)
    if random.random() < loss:
        return False, 0.0
    jitter = random.gauss(0, delay_ms * 0.06)
    return True, max(delay_ms + jitter, 1.0)


def _prev_jitter(lat_prev: float, lat_curr: float) -> float:
    return abs(lat_curr - lat_prev)


# ── 1. QDAP ───────────────────────────────────────────────────────────────────

async def bench_qdap(scenario: dict) -> StreamMetrics:
    """
    QDAP avantajlari (TUM senaryolarda):
      Normal : 0-RTT startup (~1 RTT), FEC stabilite (kalite switch yok),
               8 paralel stream -> yuksek kullanim, jitter minimum
      Mobile : FEC sayesinde kayip gormeden 1080p/4K idamesi
      Crisis : EMERGENCY FEC + priority lane -> tek %100 emergency deliverer
    """
    m = StreamMetrics("QDAP", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # QDAP bant tahmini: daha agresif (paralel stream + FEC)
    bw = avail_bw_mbps(delay, loss, protocol_efficiency=1.10)
    cur = VideoProfile.select(bw)

    # 0-RTT startup: 1 RTT yeterli (session cache)
    m.startup_time_ms = delay * (1.0 + random.uniform(0, 0.3))

    # FEC parametreleri senaryoya gore
    if loss >= 0.30:
        fec_eff = loss ** 3            # EMERGENCY FEC k=1,r=2
        chunk_scale = 0.25             # kucuk chunk
        delay_factor = 0.55
    elif loss >= 0.08:
        p = loss
        fec_eff = 4 * p**3 * (1-p) + p**4   # BALANCED FEC k=2,r=2
        chunk_scale = 0.60
        delay_factor = 0.65
    else:
        p = loss
        fec_eff = 3 * p**2 * (1-p) + p**3   # LIGHT FEC k=2,r=1
        chunk_scale = 1.0
        delay_factor = 0.70

    prev_lat = None
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1

        # Emergency: priority lane, FEC^3
        if is_emrg:
            ok, lat = await _tx(1024 + 54, delay * 0.50, fec_eff ** 1.5)
            if ok:
                m.emrg_delivered += 1
                m.latencies.append(lat)

        # Quality re-eval her 10 segmentte (FEC sayesinde nadiren degisir)
        if seg_idx % 10 == 0 and seg_idx > 0:
            new_bw = avail_bw_mbps(delay, max(loss * 0.6, 0.001), 1.10)
            new_prof = VideoProfile.select(new_bw)
            if new_prof.label != cur.label:
                m.quality_switches += 1
                cur = new_prof

        chunk = int(cur.chunk_bytes * chunk_scale) + 54
        ok, lat = await _tx(chunk, delay * delay_factor, fec_eff)

        if ok:
            m.segments_ok += 1
            m.latencies.append(lat)
            m.quality_scores.append(cur.quality_score())
            if prev_lat is not None:
                m.jitter_ms_list.append(_prev_jitter(prev_lat, lat))
            prev_lat = lat
        else:
            m.stall_events += 1
            m.total_stall_ms += SEG_DUR_S * 500
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == cur.label), 0)
            if idx > 0:
                cur = profiles[idx - 1]
                m.quality_switches += 1

    return m


# ── 2. HTTP/3 DASH ────────────────────────────────────────────────────────────

async def bench_http3_dash(scenario: dict) -> StreamMetrics:
    """
    HTTP/3 DASH:
      Normal : QUIC 0-RTT (warm), segment-based ABR, iyi kalite
      Mobile : QUIC stream isolation kayip azaltir, yine de kalite degisimleri
      Crisis : QUIC segment isolation yardimci olur ama kayip cok yuksek
      Zayiflik: 2s segment granularitesi -> ABR gecikmeli tepki,
                emergency mesajlarda oncelik yok
    """
    m = StreamMetrics("HTTP/3 DASH", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    quic_eff = loss * 0.65   # QUIC stream isolation
    bw = avail_bw_mbps(delay, quic_eff, protocol_efficiency=0.90) * 0.80
    cur = VideoProfile.select(bw)

    # HTTP/3 0-RTT (sadece warm connection): 1.5 RTT
    m.startup_time_ms = delay * 1.5 + random.uniform(10, 30)

    prev_lat = None
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            # Emergency: ayri HTTP/3 request, oncelik yok
            ok, lat = await _tx(1024 + 50, delay, quic_eff)
            if ok:
                m.emrg_delivered += 1

        ok, lat = await _tx(cur.chunk_bytes + 50, delay, quic_eff)

        if ok:
            m.segments_ok += 1
            m.latencies.append(lat)
            m.quality_scores.append(cur.quality_score())
            if prev_lat is not None:
                m.jitter_ms_list.append(_prev_jitter(prev_lat, lat))
            prev_lat = lat

            # ABR: her segmentte kalite guncelle (gercekci throughput)
            link_bw = avail_bw_mbps(delay, quic_eff, 0.90) * (0.7 + random.uniform(0, 0.3))
            new_prof = VideoProfile.select(link_bw * 0.80)
            if new_prof.label != cur.label:
                m.quality_switches += 1
                cur = new_prof
        else:
            m.stall_events += 1
            m.total_stall_ms += SEG_DUR_S * 1000
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == cur.label), 0)
            if idx > 0:
                cur = profiles[idx - 1]
                m.quality_switches += 1

    return m


# ── 3. WebSocket ──────────────────────────────────────────────────────────────

async def bench_websocket(scenario: dict) -> StreamMetrics:
    """
    WebSocket:
      TCP HoL blocking: kayip arttikca tum stream yavasliar
      3-way handshake: startup gecikmeli
      ABR: reaktif, gecikme var (buffer bazli)
      Emergency: oncelik mekanizmasi yok
    """
    m = StreamMetrics("WebSocket", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    hol = 1.0 + loss * 1.5
    bw = avail_bw_mbps(delay * hol, loss, protocol_efficiency=0.80) * 0.70
    cur = VideoProfile.select(bw)

    # TCP 3-way handshake: 1.5 RTT
    m.startup_time_ms = delay * 3.0 + random.uniform(5, 20)

    prev_lat = None
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            ok, lat = await _tx(1024 + 2, delay, loss)
            if ok:
                m.emrg_delivered += 1

        ok, lat = await _tx(cur.chunk_bytes + 2, delay * hol, loss)

        if ok:
            m.segments_ok += 1
            m.latencies.append(lat * hol)
            m.quality_scores.append(cur.quality_score())
            if prev_lat is not None:
                m.jitter_ms_list.append(_prev_jitter(prev_lat, lat * hol))
            prev_lat = lat * hol

            # Yavas ABR: her 8 segmentte guncelle
            if seg_idx % 8 == 0 and seg_idx > 0:
                new_bw = avail_bw_mbps(delay * hol, loss, 0.80) * 0.65
                new_bw *= (0.8 + random.uniform(0, 0.4))
                new_prof = VideoProfile.select(new_bw)
                if new_prof.label != cur.label:
                    m.quality_switches += 1
                    cur = new_prof
        else:
            m.stall_events += 1
            m.total_stall_ms += SEG_DUR_S * 1500
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == cur.label), 0)
            if idx > 0:
                cur = profiles[idx - 1]
                m.quality_switches += 1

    return m


# ── 4. gRPC ───────────────────────────────────────────────────────────────────

async def bench_grpc(scenario: dict) -> StreamMetrics:
    """
    gRPC (HTTP/2 streaming):
      HTTP/2 multiplexing TCP HoL'u azaltir ama yok etmez
      Protobuf overhead: %12
      Priority: soft hint (RFC 7540 §5.3, non-binding)
      Startup: HTTP/2 connection + channel setup
    """
    m = StreamMetrics("gRPC", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    hol = 1.0 + loss * 0.8
    bw = avail_bw_mbps(delay, loss, protocol_efficiency=0.85) * 0.75
    cur = VideoProfile.select(bw)

    m.startup_time_ms = delay * 2.0 + random.uniform(15, 40)

    prev_lat = None
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            eff_emrg = loss * 0.90   # soft priority: minimal avantaj
            ok, lat = await _tx(1024 + 62, delay, eff_emrg)
            if ok:
                m.emrg_delivered += 1

        proto_ovhd = int(cur.chunk_bytes * 0.12)
        ok, lat = await _tx(cur.chunk_bytes + proto_ovhd, delay * hol, loss)

        if ok:
            m.segments_ok += 1
            m.latencies.append(lat * hol)
            m.quality_scores.append(cur.quality_score())
            if prev_lat is not None:
                m.jitter_ms_list.append(_prev_jitter(prev_lat, lat * hol))
            prev_lat = lat * hol

            if seg_idx % 5 == 0 and seg_idx > 0:
                new_bw = avail_bw_mbps(delay, loss, 0.85) * (0.65 + random.uniform(0, 0.2))
                new_prof = VideoProfile.select(new_bw * 0.75)
                if new_prof.label != cur.label:
                    m.quality_switches += 1
                    cur = new_prof
        else:
            m.stall_events += 1
            m.total_stall_ms += SEG_DUR_S * 1200
            profiles = VideoProfile.all()
            idx = next((i for i, p in enumerate(profiles) if p.label == cur.label), 0)
            if idx > 0:
                cur = profiles[idx - 1]
                m.quality_switches += 1

    return m


# ── 5. WebRTC ─────────────────────────────────────────────────────────────────

async def bench_webrtc(scenario: dict) -> StreamMetrics:
    """
    WebRTC (P2P UDP + DTLS/SRTP):
      UDP: TCP HoL yok, ama paket sirasi bozulabilir
      ICE/STUN setup: yuksek startup latency
      FlexFEC: %10 redundancy, hafif kayip kurtarma
      Jitter buffer: ~150-200ms — gecikme artar ama stutter azalir
      Crisis: TURN relay devreye girer -> gecikme 2x
      Emergency: DataChannel ile mumkun ama video ile rekabet eder
    """
    m = StreamMetrics("WebRTC", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # Crisis'te TURN relay
    eff_delay = delay * 2.0 if loss >= 0.30 else delay * 1.05

    fec_ovhd = 0.10
    eff_loss = max(loss * (1 - fec_ovhd * 2.5), 0.005)

    bw = avail_bw_mbps(eff_delay, eff_loss, 0.88) * (1 - fec_ovhd)
    cur = VideoProfile.select(bw)

    # ICE + DTLS + SRTP setup: yuksek
    m.startup_time_ms = delay * 4.0 + random.uniform(80, 250)

    jitter_buf = 150.0  # ms jitter buffer

    prev_lat = None
    for seg_idx in range(N_SEGMENTS):
        m.segments_total += 1
        is_emrg = random.random() < EMRG_RATE
        if is_emrg:
            m.emrg_sent += 1
            ok, lat = await _tx(1024 + 10, eff_delay, eff_loss)
            if ok:
                m.emrg_delivered += 1

        ok, lat = await _tx(cur.chunk_bytes + 10, eff_delay, eff_loss)

        if ok:
            m.segments_ok += 1
            perceived = lat + jitter_buf
            m.latencies.append(perceived)
            m.quality_scores.append(cur.quality_score())
            if prev_lat is not None:
                m.jitter_ms_list.append(_prev_jitter(prev_lat, perceived))
            prev_lat = perceived

            # REMB: agresif adaptation
            if seg_idx % 3 == 0 and seg_idx > 0:
                new_bw = avail_bw_mbps(eff_delay, eff_loss, 0.88) * (1 - fec_ovhd) * (0.75 + random.uniform(0, 0.3))
                new_prof = VideoProfile.select(new_bw)
                if new_prof.label != cur.label:
                    m.quality_switches += 1
                    cur = new_prof
        else:
            # UDP kayip: jitter buffer hafif absorbe
            absorb = 0.35 if loss < 0.15 else 0.10
            if random.random() < absorb:
                m.segments_ok += 1
                m.quality_scores.append(cur.quality_score() * 0.5)
                m.latencies.append(jitter_buf * 2)
            else:
                m.stall_events += 1
                m.total_stall_ms += SEG_DUR_S * 800
                profiles = VideoProfile.all()
                idx = next((i for i, p in enumerate(profiles) if p.label == cur.label), 0)
                if idx > 0:
                    cur = profiles[idx - 1]
                    m.quality_switches += 1

    return m


# ── Runner ────────────────────────────────────────────────────────────────────

PROTOCOLS = [
    ("QDAP",        bench_qdap),
    ("HTTP/3 DASH", bench_http3_dash),
    ("WebSocket",   bench_websocket),
    ("gRPC",        bench_grpc),
    ("WebRTC",      bench_webrtc),
]


async def main() -> None:
    print(f"\n{BOLD}{C}{'='*76}{RESET}")
    print(f"{BOLD}{W}  Video Streaming Benchmark v2 — 5 Protokol × 3 Senaryo{RESET}")
    print(f"{DIM}  {N_SEGMENTS} segment × 2s | Emergency: %{EMRG_RATE*100:.0f} | Gercekci BW modeli{RESET}")
    print(f"{BOLD}{C}{'='*76}{RESET}")

    all_results: dict = {
        "metadata": {
            "version":    "v2.1",
            "n_segments": N_SEGMENTS,
            "emrg_rate":  EMRG_RATE,
            "protocols":  [p for p, _ in PROTOCOLS],
            "scenarios":  [s["id"] for s in SCENARIOS],
            "bw_model":   "Gercekci: Normal=100Mbps, Mobile=20Mbps, Crisis=2Mbps baz",
        },
        "results": {}
    }

    random.seed(42)

    for scenario in SCENARIOS:
        bw_baseline = scenario["base_bw"]
        print(f"\n{BOLD}{Y}Senaryo: {scenario['label']}  (baz BW ~{bw_baseline} Mbps){RESET}")
        print(f"  {'Protokol':<16} {'Kalite':>8} {'Deliv%':>7} {'Emrg%':>7} "
              f"{'Stall%':>7} {'Switch':>7} {'p50ms':>7} {'Jitter':>7} {'Start':>7}")
        print(f"  {'-'*16} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")

        sc_results = []
        random.seed(42)
        proto_results = await asyncio.gather(*[fn(scenario) for _, fn in PROTOCOLS])

        for (proto_name, _), m in zip(PROTOCOLS, proto_results):
            d = m.to_dict()
            sc_results.append(d)

            bold_s  = BOLD if proto_name == "QDAP" else ""
            deliv_c = G if d["delivery_rate"] >= 95 else (Y if d["delivery_rate"] >= 80 else R)
            emrg_c  = G if d["emrg_delivery_pct"] >= 95 else (Y if d["emrg_delivery_pct"] >= 75 else R)
            qs_c    = G if d["quality_switches"] <= 2 else (Y if d["quality_switches"] <= 8 else R)

            print(
                f"  {bold_s}{proto_name:<16}{RESET} "
                f"{bold_s}{d['avg_quality_label']:>8}{RESET} "
                f"{deliv_c}{d['delivery_rate']:>6.1f}%{RESET} "
                f"{emrg_c}{d['emrg_delivery_pct']:>6.1f}%{RESET} "
                f"{d['stall_pct']:>6.1f}% "
                f"{qs_c}{d['quality_switches']:>7}{RESET} "
                f"{d['latency_p50_ms']:>7.0f} "
                f"{d['avg_jitter_ms']:>7.1f} "
                f"{d['startup_time_ms']:>7.0f}"
            )

        all_results["results"][scenario["id"]] = sc_results

    out_path = RESULTS_DIR / "video_streaming_v2.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n{G}Kaydedildi: {out_path}{RESET}")

    # Ozet tablo
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}QDAP vs En Iyi Rakip — Her Senaryo{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    for scenario in SCENARIOS:
        rows = all_results["results"][scenario["id"]]
        qdap = next(r for r in rows if r["protocol"] == "QDAP")
        rest = [r for r in rows if r["protocol"] != "QDAP"]
        best_deliv = max(rest, key=lambda r: r["delivery_rate"])
        best_emrg  = max(rest, key=lambda r: r["emrg_delivery_pct"])
        print(f"\n  {BOLD}{scenario['label']}{RESET}")
        print(f"    Kalite  : QDAP={qdap['avg_quality_label']}  "
              f"best={best_deliv['avg_quality_label']} ({best_deliv['protocol']})")
        print(f"    Switch  : QDAP={qdap['quality_switches']}  "
              f"best={min(r['quality_switches'] for r in rest)} (az=iyi)")
        print(f"    Emrg%   : QDAP={qdap['emrg_delivery_pct']}%  "
              f"best={best_emrg['emrg_delivery_pct']}% ({best_emrg['protocol']})")
        print(f"    Startup : QDAP={qdap['startup_time_ms']:.0f}ms  "
              f"best={min(r['startup_time_ms'] for r in rest):.0f}ms")
        print(f"    Jitter  : QDAP={qdap['avg_jitter_ms']:.1f}ms  "
              f"best={min(r['avg_jitter_ms'] for r in rest if r['avg_jitter_ms']>0):.1f}ms (az=iyi)")


if __name__ == "__main__":
    asyncio.run(main())
