#!/usr/bin/env python3
"""
File Transfer Benchmark — QDAP vs HTTP/1.1, HTTP/2, HTTP/3, Raw TCP
=====================================================================
Farkli boyutlarda dosyalarin farkli ag kosullarinda iletim performansi.

NOT: Agir simulasyon yerine analitik model kullanilir (Mathis + kayip teorisi).
Her trial'da random jitter eklenerek N_TRIALS tekrar yapilir.

Dosya boyutlari:
  - Tiny  :    1 KB
  - Small :   64 KB
  - Medium:    1 MB
  - Large :   10 MB

Senaryolar:
  1. Normal   : 20ms  / 1%   loss
  2. Mobile   : 80ms  / 8%   loss
  3. Crisis   : 300ms / 35%  loss

Protokoller:
  1. QDAP        — parallel streaming + adaptive chunking + FEC
  2. HTTP/1.1    — sequential, HoL blocking
  3. HTTP/2      — multiplexed 6 streams
  4. HTTP/3 QUIC — QUIC transport, 0-RTT
  5. Raw TCP     — baseline
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
C = "\033[96m"; W = "\033[97m"; BOLD = "\033[1m"
DIM = "\033[2m"; RESET = "\033[0m"

FILE_SIZES = [
    ("Tiny   1KB",     1_024),
    ("Small  64KB",   65_536),
    ("Medium 1MB",  1_048_576),
    ("Large  10MB",10_485_760),
]

SCENARIOS = [
    {"id": "normal", "label": "Normal (20ms/1%)",   "delay_ms": 20,  "loss": 0.01},
    {"id": "mobile", "label": "Mobile (80ms/8%)",   "delay_ms": 80,  "loss": 0.08},
    {"id": "crisis", "label": "Crisis (300ms/35%)", "delay_ms": 300, "loss": 0.35},
]

N_TRIALS = 30


# ── Analitik transfer modeli ───────────────────────────────────────────────────

def effective_loss_fec(loss: float, k: int, r: int) -> float:
    """
    Sistematik (k, r) FEC sonrasi efektif kayip olasiligi.
    k veri paketi + r yedek paketle r+1 adede kadar kayip kurtarilir.
    """
    # P(kayip > r) = sum_{i=r+1}^{k+r} C(k+r,i) * p^i * (1-p)^(k+r-i)
    n = k + r
    p = loss
    q = 1 - p
    prob_fail = 0.0
    for i in range(r + 1, n + 1):
        coeff = math.comb(n, i)
        prob_fail += coeff * (p ** i) * (q ** (n - i))
    return min(prob_fail, 1.0)


def transfer_time_ms(
    file_size_bytes: int,
    delay_ms: float,
    loss: float,
    chunk_size: int,
    overhead_per_chunk: int,
    parallel_streams: int,
    max_retries: int,
    effective_loss_override: float = None,
    rng: random.Random = None,
) -> tuple[bool, float, float, int]:
    """
    Dosya transferi sure tahmini (analitik + jitter).

    Returns: (success, completion_ms, ttfb_ms, retransmit_count)
    """
    if rng is None:
        rng = random.Random()

    eff_loss = effective_loss_override if effective_loss_override is not None else loss

    n_chunks = math.ceil(file_size_bytes / chunk_size)
    chunks_per_stream = math.ceil(n_chunks / parallel_streams)

    # Her chunk icin ortalama deneme sayisi: 1 / (1 - eff_loss)
    avg_attempts = 1 / max(1 - eff_loss, 0.01)
    avg_attempts = min(avg_attempts, max_retries + 1)

    # Toplam gecikme: chunk_per_stream * avg_attempts * RTT
    rtt = delay_ms * 2  # one-way × 2

    # Jitter (±%15)
    jitter_factor = 1.0 + rng.gauss(0, 0.08)

    # TTFB: ilk chunk'in gelmesi = delay + overhead
    ttfb_ms = delay_ms * jitter_factor + overhead_per_chunk * 0.001

    # Tum stream'lerin tamamlanma suresi
    # En yavash stream belirler; Normal dagilim ile en yavash tahmin
    stream_time_ms = chunks_per_stream * avg_attempts * rtt * jitter_factor
    # Paralel stream'ler arasi en yavash (order statistic approximation)
    if parallel_streams > 1:
        stream_time_ms *= (1 + math.log(parallel_streams) * 0.05)

    completion_ms = stream_time_ms

    # Retransmit sayisi
    retransmit_count = int(n_chunks * (avg_attempts - 1) + rng.expovariate(1))

    # Transfer basarisi: tum chunk'larin max_retries icinde gelmesi gerekiyor
    # P(basarisiz) = P(herhangi bir chunk max_retries+1 denemede basarisiz)
    p_chunk_fail = eff_loss ** (max_retries + 1)
    p_transfer_fail = 1 - (1 - p_chunk_fail) ** n_chunks
    success = rng.random() >= p_transfer_fail

    return success, completion_ms, ttfb_ms, retransmit_count


# ── Metrik ────────────────────────────────────────────────────────────────────

@dataclass
class TransferResult:
    protocol:    str
    file_label:  str
    file_size:   int
    scenario:    str
    completion_times_ms: List[float] = field(default_factory=list)
    ttfb_ms:             List[float] = field(default_factory=list)
    retransmit_counts:   List[int]   = field(default_factory=list)
    successes: int = 0
    trials:    int = 0

    def success_rate(self) -> float:
        return self.successes / max(self.trials, 1) * 100

    def p50_ms(self) -> float:
        return statistics.median(self.completion_times_ms) if self.completion_times_ms else 0.0

    def p99_ms(self) -> float:
        if not self.completion_times_ms:
            return 0.0
        s = sorted(self.completion_times_ms)
        return s[int(len(s) * 0.99)] if len(s) > 10 else s[-1]

    def throughput_mbps(self) -> float:
        if not self.completion_times_ms:
            return 0.0
        p50_s = self.p50_ms() / 1000.0
        return (self.file_size * 8) / (p50_s * 1e6)

    def avg_ttfb(self) -> float:
        return statistics.mean(self.ttfb_ms) if self.ttfb_ms else 0.0

    def avg_retransmits(self) -> float:
        return statistics.mean(self.retransmit_counts) if self.retransmit_counts else 0.0

    def to_dict(self) -> dict:
        return {
            "protocol":          self.protocol,
            "file_label":        self.file_label,
            "file_size_bytes":   self.file_size,
            "scenario":          self.scenario,
            "trials":            self.trials,
            "success_rate":      round(self.success_rate(), 1),
            "p50_completion_ms": round(self.p50_ms(), 1),
            "p99_completion_ms": round(self.p99_ms(), 1),
            "throughput_mbps":   round(self.throughput_mbps(), 4),
            "avg_ttfb_ms":       round(self.avg_ttfb(), 1),
            "avg_retransmits":   round(self.avg_retransmits(), 1),
        }


# ── Protokol benchmark'lari ───────────────────────────────────────────────────

def bench_qdap(file_size: int, scenario: dict, rng: random.Random) -> TransferResult:
    r = TransferResult("QDAP", "", file_size, scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # QFT Scheduler separates emergency (small) vs bulk (large) payload paths.
    # Emergency uses MICRO chunks (4KB) + EMERGENCY FEC for maximum delivery rate.
    # Bulk uses LARGE chunks (32-65KB) + BALANCED FEC for RTT-efficient streaming.
    # This matches what select_bulk_fec_profile() + QFTScheduler.decide() produce.
    is_bulk = file_size >= 256_000   # ≥256KB → streaming / bulk transfer path

    if loss >= 0.30:
        if is_bulk:
            # LARGE chunks: RTT cost of retry dominates → aggressive FEC reduces retries
            # QFT picks LARGE/JUMBO, AdaptiveFEC.encode(is_bulk=True) → BALANCED(k=2,r=2)
            # p_eff at 35% loss = 12.6%  (vs 35% raw, vs 4.3% for EMERGENCY)
            chunk        = 32_768
            fec_k, fec_r = 2, 2   # BALANCED: any 3-of-4 recovers
            retries      = 3
            delay_factor = 0.58   # Ghost Session removes ACK RTT from hot path
        else:
            # Small emergency message: MICRO + EMERGENCY FEC (p_eff=4.3%)
            chunk        = 4_096
            fec_k, fec_r = 1, 2
            retries      = 4
            delay_factor = 0.60
    elif loss >= 0.10:
        if is_bulk:
            chunk        = 65_536
            fec_k, fec_r = 2, 1   # RELIABLE: p_eff=28.2%
            retries      = 2
            delay_factor = 0.65
        else:
            chunk        = 16_384
            fec_k, fec_r = 2, 2
            retries      = 3
            delay_factor = 0.65
    else:
        chunk        = 65_536
        fec_k, fec_r = 2, 1
        retries      = 1
        delay_factor = 0.70

    eff = effective_loss_fec(loss, k=fec_k, r=fec_r)

    for _ in range(N_TRIALS):
        r.trials += 1
        ok, t, ttfb, retx = transfer_time_ms(
            file_size, delay * delay_factor, loss,
            chunk_size=chunk,
            overhead_per_chunk=54,
            parallel_streams=8,
            max_retries=retries,
            effective_loss_override=eff,
            rng=rng,
        )
        if ok:
            r.successes += 1
            r.completion_times_ms.append(t)
            r.ttfb_ms.append(ttfb)
            r.retransmit_counts.append(retx)

    return r


def bench_http11(file_size: int, scenario: dict, rng: random.Random) -> TransferResult:
    r = TransferResult("HTTP/1.1", "", file_size, scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    for _ in range(N_TRIALS):
        r.trials += 1
        ok, t, ttfb, retx = transfer_time_ms(
            file_size, delay, loss,
            chunk_size=8_192,
            overhead_per_chunk=200,
            parallel_streams=1,
            max_retries=3,
            rng=rng,
        )
        if ok:
            r.successes += 1
            r.completion_times_ms.append(t)
            r.ttfb_ms.append(ttfb)
            r.retransmit_counts.append(retx)

    return r


def bench_http2(file_size: int, scenario: dict, rng: random.Random) -> TransferResult:
    r = TransferResult("HTTP/2", "", file_size, scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # TCP HoL: loss arttikca gecikme buyur
    hol = 1.0 + loss * 1.5

    for _ in range(N_TRIALS):
        r.trials += 1
        ok, t, ttfb, retx = transfer_time_ms(
            file_size, delay * hol, loss,
            chunk_size=32_768,
            overhead_per_chunk=50,
            parallel_streams=6,
            max_retries=3,
            rng=rng,
        )
        if ok:
            r.successes += 1
            r.completion_times_ms.append(t)
            r.ttfb_ms.append(ttfb)
            r.retransmit_counts.append(retx)

    return r


def bench_http3(file_size: int, scenario: dict, rng: random.Random) -> TransferResult:
    r = TransferResult("HTTP/3 QUIC", "", file_size, scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # QUIC: stream isolation -> effective loss azalir
    eff = loss * 0.60

    for _ in range(N_TRIALS):
        r.trials += 1
        ok, t, ttfb, retx = transfer_time_ms(
            file_size, delay * 0.90, loss,
            chunk_size=32_768,
            overhead_per_chunk=25,
            parallel_streams=8,
            max_retries=2,
            effective_loss_override=eff,
            rng=rng,
        )
        if ok:
            r.successes += 1
            r.completion_times_ms.append(t)
            r.ttfb_ms.append(ttfb)
            r.retransmit_counts.append(retx)

    return r


def bench_raw_tcp(file_size: int, scenario: dict, rng: random.Random) -> TransferResult:
    r = TransferResult("Raw TCP", "", file_size, scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]

    # Slow start: crisis'te cwnd kucuk kalir
    if loss >= 0.30:
        chunk = 2_896
    elif loss >= 0.10:
        chunk = 7_240
    else:
        chunk = 14_480

    for _ in range(N_TRIALS):
        r.trials += 1
        ok, t, ttfb, retx = transfer_time_ms(
            file_size, delay, loss,
            chunk_size=chunk,
            overhead_per_chunk=20,
            parallel_streams=1,
            max_retries=5,
            rng=rng,
        )
        if ok:
            r.successes += 1
            r.completion_times_ms.append(t)
            r.ttfb_ms.append(ttfb)
            r.retransmit_counts.append(retx)

    return r


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> None:
    t_start = time.perf_counter()
    print(f"\n{BOLD}{W}Dosya Transfer Benchmark — QDAP vs HTTP/1.1 / HTTP/2 / HTTP/3 / Raw TCP{RESET}")
    print(f"{DIM}{N_TRIALS} trial/kombinasyon · {len(FILE_SIZES)} dosya boyutu · {len(SCENARIOS)} senaryo{RESET}")

    BENCHMARKS = [
        ("QDAP",        bench_qdap),
        ("HTTP/1.1",    bench_http11),
        ("HTTP/2",      bench_http2),
        ("HTTP/3 QUIC", bench_http3),
        ("Raw TCP",     bench_raw_tcp),
    ]

    all_results: dict = {
        "metadata": {
            "n_trials":   N_TRIALS,
            "file_sizes": [(lbl, sz) for lbl, sz in FILE_SIZES],
            "scenarios":  [s["id"] for s in SCENARIOS],
            "protocols":  [n for n, _ in BENCHMARKS],
            "note":       "Analitik model: Mathis throughput + FEC kayip azaltma",
        },
        "results": {}
    }

    rng = random.Random(42)

    for scenario in SCENARIOS:
        print(f"\n{BOLD}{C}Senaryo: {scenario['label']}{RESET}")
        header = f"  {'Protokol':<14}"
        for lbl, _ in FILE_SIZES:
            short = lbl.split()[0] + lbl.split()[1]
            header += f" {short:>10}"
        header += f" {'Succ%':>7}"
        print(header)
        print(f"  {'-'*14}" + f" {'-'*10}" * len(FILE_SIZES) + f" {'-'*7}")

        scen_results: Dict[str, List[dict]] = {lbl.strip(): [] for lbl, _ in FILE_SIZES}

        for proto_name, bench_fn in BENCHMARKS:
            row_times = []
            all_succ  = []

            for file_label, file_size in FILE_SIZES:
                res = bench_fn(file_size, scenario, rng)
                res.file_label = file_label
                d = res.to_dict()
                scen_results[file_label.strip()].append(d)
                row_times.append(d["p50_completion_ms"])
                all_succ.append(d["success_rate"])

            color  = G if proto_name == "QDAP" else RESET
            avg_s  = statistics.mean(all_succ)
            sc_col = G if avg_s >= 90 else (Y if avg_s >= 70 else R)

            line = f"  {color}{proto_name:<14}{RESET}"
            for t in row_times:
                line += f" {t:>10.0f}"
            line += f" {sc_col}{avg_s:>6.1f}%{RESET}"
            print(line)

        all_results["results"][scenario["id"]] = scen_results

    # JSON kaydet
    out_path = RESULTS_DIR / "file_transfer.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    elapsed = time.perf_counter() - t_start
    print(f"\n{G}Sonuclar kaydedildi: {out_path} ({elapsed:.1f}s){RESET}")

    # Crisis 10MB ozet
    print(f"\n{BOLD}Crisis (300ms/35%loss) — 10MB Transferi (p50 ms, throughput, succ%){RESET}")
    crisis_10mb = all_results["results"]["crisis"].get("Large  10MB", [])
    if crisis_10mb:
        sorted_rows = sorted(crisis_10mb, key=lambda r: r["p50_completion_ms"])
        for r in sorted_rows:
            color = G if r["protocol"] == "QDAP" else RESET
            print(f"  {color}{r['protocol']:<14}{RESET}"
                  f"  p50={r['p50_completion_ms']:>8.0f}ms"
                  f"  {r['throughput_mbps']:>7.4f} Mbps"
                  f"  succ={r['success_rate']:>5.1f}%"
                  f"  retx={r['avg_retransmits']:>6.1f}")


if __name__ == "__main__":
    main()
