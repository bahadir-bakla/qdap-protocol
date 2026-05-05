#!/usr/bin/env python3
"""
Streaming Flow Benchmark — QDAP vs HTTP/1.1 / WebSocket / gRPC
================================================================
Sürekli veri akışı (video feed, IoT sensor stream, live telemetry).

Hedef: bir protokolün belirli bir bitrate'i ne kadar tutarlı iletebileceğini ölçmek.
Sadece teslim oranı değil; JİTTER ve STALL oranı da ölçülür.

Metrikler:
  actual_mbps    — gerçekten teslim edilen bant genişliği
  target_mbps    — hedef bant genişliği
  efficiency     — actual / target (ne kadar hedefe yaklaştı)
  jitter_ms      — chunk varış arası süre standart sapması
  stall_count    — 2× beklenen aralıktan uzun gecikme sayısı
  stall_rate_pct — stall_count / total_chunks * 100
  p50/p99_ms     — chunk başına gecikme yüzdelikleri

Senaryolar:
  Normal (0ms / 0% loss)
  Mobile (80ms / 5% loss)
  Crisis (300ms / 30% loss)

Kullanim:
  python benchmarks/streaming_benchmark.py
  python benchmarks/streaming_benchmark.py --target 20  # 20 Mbps hedef
  python benchmarks/streaming_benchmark.py --server <IP>  # WAN modu (wan_server.py gerekir)
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Parametreler ──────────────────────────────────────────────────────────────

CHUNK_SIZE    = 4096         # 4 KB — tipik ağ segmenti
STREAM_SEC    = 15           # saniye başına ölçüm süresi
STALL_MULT    = 2.5          # beklenen aralığın kaç katı = stall

TARGET_MBPS_DEFAULT = 5.0   # varsayılan hedef bitrate

SCENARIOS = [
    {"id": "normal", "label": "Normal   (0ms / 0%)",    "delay_ms": 0,   "loss": 0.000},
    {"id": "mobile", "label": "Mobile   (80ms / 5%)",   "delay_ms": 80,  "loss": 0.050},
    {"id": "crisis", "label": "Crisis   (300ms / 30%)", "delay_ms": 300, "loss": 0.300},
]

# ── Network injection ─────────────────────────────────────────────────────────

_delay_ms: float = 0.0
_loss:      float = 0.0


async def _net(payload_size: int = 0) -> bool:
    """Delay + drop simulate — loopback injection."""
    if _delay_ms > 0:
        j = random.gauss(0, _delay_ms * 0.1)
        await asyncio.sleep(max(_delay_ms + j, 0) / 1000.0)
    return not (_loss > 0 and random.random() < _loss)


def set_net(delay_ms: float, loss: float) -> None:
    global _delay_ms, _loss
    _delay_ms, _loss = delay_ms, loss


# ── Metrik ────────────────────────────────────────────────────────────────────

@dataclass
class StreamResult:
    protocol:     str
    scenario:     str
    target_mbps:  float
    sent_chunks:  int   = 0
    recvd_chunks: int   = 0
    bytes_ok:     int   = 0
    duration_s:   float = 0.0
    arrivals:     List[float] = field(default_factory=list)  # chunk varış zamanları
    errors:       int   = 0

    def actual_mbps(self) -> float:
        return (self.bytes_ok * 8) / (max(self.duration_s, 0.001) * 1e6)

    def efficiency(self) -> float:
        return min(self.actual_mbps() / max(self.target_mbps, 0.001) * 100, 100.0)

    def jitter_ms(self) -> float:
        if len(self.arrivals) < 3:
            return 0.0
        intervals = [(self.arrivals[i+1] - self.arrivals[i]) * 1000
                     for i in range(len(self.arrivals) - 1)]
        return statistics.stdev(intervals) if len(intervals) > 1 else 0.0

    def expected_interval_ms(self) -> float:
        """Hedef bitrate'e gore beklenen chunk arası ms."""
        return (CHUNK_SIZE * 8) / (max(self.target_mbps, 0.001) * 1e6) * 1000

    def stall_count(self) -> int:
        if len(self.arrivals) < 2:
            return 0
        expected = self.expected_interval_ms() / 1000.0
        threshold = expected * STALL_MULT
        return sum(
            1 for i in range(len(self.arrivals) - 1)
            if (self.arrivals[i+1] - self.arrivals[i]) > threshold
        )

    def stall_rate(self) -> float:
        if self.sent_chunks == 0:
            return 0.0
        return self.stall_count() / max(self.sent_chunks - 1, 1) * 100

    def p50_ms(self) -> float:
        if len(self.arrivals) < 2:
            return 0.0
        intervals = [(self.arrivals[i+1] - self.arrivals[i]) * 1000
                     for i in range(len(self.arrivals) - 1)]
        return statistics.median(intervals) if intervals else 0.0

    def p99_ms(self) -> float:
        if len(self.arrivals) < 2:
            return 0.0
        intervals = sorted(
            (self.arrivals[i+1] - self.arrivals[i]) * 1000
            for i in range(len(self.arrivals) - 1)
        )
        return intervals[int(len(intervals) * 0.99)] if intervals else 0.0

    def delivery_rate(self) -> float:
        return self.recvd_chunks / max(self.sent_chunks, 1) * 100

    def to_dict(self) -> dict:
        return {
            "protocol":     self.protocol,
            "scenario":     self.scenario,
            "target_mbps":  self.target_mbps,
            "actual_mbps":  round(self.actual_mbps(), 4),
            "efficiency":   round(self.efficiency(), 2),
            "delivery_pct": round(self.delivery_rate(), 2),
            "jitter_ms":    round(self.jitter_ms(), 2),
            "stall_count":  self.stall_count(),
            "stall_rate":   round(self.stall_rate(), 2),
            "p50_ms":       round(self.p50_ms(), 2),
            "p99_ms":       round(self.p99_ms(), 2),
            "sent_chunks":  self.sent_chunks,
            "recvd_chunks": self.recvd_chunks,
            "errors":       self.errors,
        }


# ── 1. QDAP — GhostSession fire-and-forget pipeline ──────────────────────────

async def bench_qdap_stream(scenario: dict, target_mbps: float) -> StreamResult:
    """
    QDAP pipeline: chunk'ları ACK beklemeden gönder.
    Alici taraf server callback'i ile sayılır.
    Stall/jitter: gönderici pipeline hızından hesaplanır.
    """
    from qdap.server import QDAPServer, QDAPClient

    r = StreamResult("QDAP", scenario["label"], target_mbps)
    PORT = 29001

    recv_times: List[float] = []

    def on_frame(frame, addr):
        recv_times.append(time.perf_counter())

    server = QDAPServer(host="127.0.0.1", port=PORT)
    server.on_frame(on_frame)
    await server.start()
    await asyncio.sleep(0.05)

    client = QDAPClient(host="127.0.0.1", port=PORT)
    await client.connect()

    # Chunk başına bekleme süresi (hedef bitrate'i aşmamak için)
    chunk_interval_s = (CHUNK_SIZE * 8) / (target_mbps * 1e6)
    chunk = b"Q" * CHUNK_SIZE

    set_net(scenario["delay_ms"], scenario["loss"])
    t0 = time.perf_counter()
    deadline = t0 + STREAM_SEC

    while time.perf_counter() < deadline:
        r.sent_chunks += 1
        ok = await _net(CHUNK_SIZE)
        if ok:
            try:
                await client.send_multiframe([chunk], priorities=[0.7])
                r.bytes_ok += CHUNK_SIZE
            except Exception:
                r.errors += 1

        # Hedef rate'i koru
        elapsed = time.perf_counter() - t0
        expected = r.sent_chunks * chunk_interval_s
        sleep = expected - elapsed
        if sleep > 0.0005:
            await asyncio.sleep(sleep)

    r.duration_s = time.perf_counter() - t0
    set_net(0, 0)

    # Server'in geri kalanları alması icin kisa bekleme
    await asyncio.sleep(min(scenario["delay_ms"] / 1000.0 * 2 + 0.3, 3.0))

    r.recvd_chunks = len(recv_times)
    r.arrivals = recv_times

    await client.close()
    await server.stop()
    await asyncio.sleep(0.3)
    return r


# ── 2. HTTP/1.1 — POST per chunk ─────────────────────────────────────────────

async def bench_http_stream(scenario: dict, target_mbps: float) -> StreamResult:
    import aiohttp
    from aiohttp import web

    r = StreamResult("HTTP/1.1", scenario["label"], target_mbps)
    PORT = 29002

    recv_times: List[float] = []

    async def handle(request: web.Request) -> web.Response:
        await request.read()
        recv_times.append(time.perf_counter())
        return web.Response(body=b"ok", status=200)

    app    = web.Application()
    app.router.add_post("/chunk", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    chunk = b"H" * CHUNK_SIZE
    chunk_interval_s = (CHUNK_SIZE * 8) / (target_mbps * 1e6)

    set_net(scenario["delay_ms"], scenario["loss"])
    t0 = time.perf_counter()
    deadline = t0 + STREAM_SEC

    connector = aiohttp.TCPConnector(limit=1)
    timeout   = aiohttp.ClientTimeout(total=max(scenario["delay_ms"] / 500.0 + 2.0, 3.0))

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        while time.perf_counter() < deadline:
            r.sent_chunks += 1
            ok = await _net(CHUNK_SIZE)
            if ok:
                try:
                    async with session.post(
                        f"http://127.0.0.1:{PORT}/chunk", data=chunk
                    ) as resp:
                        await resp.read()
                        if resp.status == 200:
                            r.bytes_ok += CHUNK_SIZE
                except Exception:
                    r.errors += 1

            elapsed  = time.perf_counter() - t0
            expected = r.sent_chunks * chunk_interval_s
            sleep    = expected - elapsed
            if sleep > 0.0005:
                await asyncio.sleep(sleep)

    r.duration_s = time.perf_counter() - t0
    set_net(0, 0)
    r.recvd_chunks = len(recv_times)
    r.arrivals = recv_times

    await runner.cleanup()
    return r


# ── 3. WebSocket — binary frame stream ───────────────────────────────────────

async def bench_ws_stream(scenario: dict, target_mbps: float) -> StreamResult:
    import websockets
    from websockets.server import serve as ws_serve

    r = StreamResult("WebSocket", scenario["label"], target_mbps)
    PORT = 29003

    recv_times: List[float] = []

    async def handler(ws):
        async for _ in ws:
            recv_times.append(time.perf_counter())
            await ws.send(b"ok")

    server = await ws_serve(handler, "127.0.0.1", PORT)

    chunk = b"W" * CHUNK_SIZE
    chunk_interval_s = (CHUNK_SIZE * 8) / (target_mbps * 1e6)

    set_net(scenario["delay_ms"], scenario["loss"])
    t0 = time.perf_counter()
    deadline = t0 + STREAM_SEC

    try:
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            while time.perf_counter() < deadline:
                r.sent_chunks += 1
                ok = await _net(CHUNK_SIZE)
                if ok:
                    try:
                        await ws.send(chunk)
                        await asyncio.wait_for(ws.recv(), timeout=max(scenario["delay_ms"] / 500.0 + 1.0, 2.0))
                        r.bytes_ok += CHUNK_SIZE
                    except Exception:
                        r.errors += 1

                elapsed  = time.perf_counter() - t0
                expected = r.sent_chunks * chunk_interval_s
                sleep    = expected - elapsed
                if sleep > 0.0005:
                    await asyncio.sleep(sleep)
    except Exception as e:
        r.errors += r.sent_chunks - r.recvd_chunks

    r.duration_s = time.perf_counter() - t0
    set_net(0, 0)
    r.recvd_chunks = len(recv_times)
    r.arrivals = recv_times

    server.close()
    await server.wait_closed()
    return r


# ── 4. QDAP Parallel — 8 stream ──────────────────────────────────────────────

async def bench_qdap_parallel(scenario: dict, target_mbps: float) -> StreamResult:
    """
    QDAP ParallelSender: 8 eşzamanlı stream.
    Yüksek bitrate hedeflerinde (20 Mbps+) tek stream'den daha iyi throughput.
    """
    from qdap.transport.parallel_sender import ParallelSender

    r = StreamResult("QDAP-8stream", scenario["label"], target_mbps)
    N_STREAMS = 8
    CHUNK_PER_STREAM = CHUNK_SIZE

    chunk = b"P" * CHUNK_PER_STREAM
    chunk_interval_s = (CHUNK_PER_STREAM * 8 * N_STREAMS) / (target_mbps * 1e6)

    set_net(scenario["delay_ms"], scenario["loss"])
    t0 = time.perf_counter()
    deadline = t0 + STREAM_SEC

    sender = ParallelSender(n_streams=N_STREAMS)

    while time.perf_counter() < deadline:
        r.sent_chunks += 1
        ok = await _net(CHUNK_PER_STREAM)
        if ok:
            try:
                results = await sender.send_chunks([chunk] * N_STREAMS)
                delivered = sum(1 for success in results if success)
                r.bytes_ok += delivered * CHUNK_PER_STREAM
                r.arrivals.append(time.perf_counter())
            except Exception:
                r.errors += 1

        elapsed  = time.perf_counter() - t0
        expected = r.sent_chunks * chunk_interval_s
        sleep    = expected - elapsed
        if sleep > 0.0005:
            await asyncio.sleep(sleep)

    r.duration_s = time.perf_counter() - t0
    set_net(0, 0)
    r.recvd_chunks = len(r.arrivals)
    return r


# ── Runner ────────────────────────────────────────────────────────────────────

R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
C = "\033[96m"; W = "\033[97m"; B = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"


def _col(val: float, good: float, ok: float, higher_is_better: bool = True) -> str:
    if higher_is_better:
        return G if val >= good else (Y if val >= ok else R)
    else:
        return G if val <= good else (Y if val <= ok else R)


def print_scenario(rows: List[StreamResult], scenario: dict, target_mbps: float) -> None:
    print(f"\n{B}{C}Senaryo: {scenario['label']}  (hedef: {target_mbps} Mbps){RESET}")
    print(
        f"  {'Protokol':<16} {'Actual':>7} {'Verim%':>7} "
        f"{'Jitter':>8} {'Stall%':>7} {'p50ms':>8} {'p99ms':>8} {'Deliv%':>7}"
    )
    print(f"  {'-'*16} {'-'*7} {'-'*7} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*7}")

    for r in rows:
        am   = r.actual_mbps()
        ef   = r.efficiency()
        ji   = r.jitter_ms()
        st   = r.stall_rate()
        p50  = r.p50_ms()
        p99  = r.p99_ms()
        dr   = r.delivery_rate()

        bold = B if "QDAP" in r.protocol else ""

        am_c = _col(ef,  90, 60)
        ji_c = _col(ji,  scenario["delay_ms"] * 0.3 + 5, scenario["delay_ms"] * 0.8 + 20, False)
        st_c = _col(st,  5, 15, False)
        dr_c = _col(dr,  95, 80)

        print(
            f"  {bold}{r.protocol:<16}{RESET} "
            f"{am_c}{am:>6.2f}M{RESET} "
            f"{am_c}{ef:>6.1f}%{RESET} "
            f"{ji_c}{ji:>7.1f}ms{RESET} "
            f"{st_c}{st:>6.1f}%{RESET} "
            f"{p50:>8.1f} {p99:>8.1f} "
            f"{dr_c}{dr:>6.1f}%{RESET}"
        )


async def run_scenario(scenario: dict, target_mbps: float) -> List[dict]:
    fns = [
        bench_qdap_stream,
        bench_http_stream,
        bench_ws_stream,
    ]
    rows = []
    for fn in fns:
        try:
            r = await fn(scenario, target_mbps)
            rows.append(r)
        except Exception as e:
            print(f"  {fn.__name__}: HATA — {e}")
    print_scenario(rows, scenario, target_mbps)
    return [r.to_dict() for r in rows]


async def main(target_mbps: float = TARGET_MBPS_DEFAULT) -> None:
    print(f"\n{B}{C}{'='*72}{RESET}")
    print(f"{B}{W}  Streaming Flow Benchmark — QDAP vs HTTP/1.1 / WebSocket{RESET}")
    print(f"{DIM}  Chunk: {CHUNK_SIZE//1024}KB  |  Sure: {STREAM_SEC}s/senaryo  "
          f"|  Hedef: {target_mbps} Mbps{RESET}")
    print(f"{DIM}  Stall = varış arası süre > {STALL_MULT}× beklenen{RESET}")
    print(f"{B}{C}{'='*72}{RESET}")

    all_results: dict = {
        "meta": {
            "chunk_size_bytes": CHUNK_SIZE,
            "stream_duration_s": STREAM_SEC,
            "target_mbps": target_mbps,
            "stall_threshold_mult": STALL_MULT,
        },
        "scenarios": {},
    }

    random.seed(42)

    for scenario in SCENARIOS:
        rows = await run_scenario(scenario, target_mbps)
        all_results["scenarios"][scenario["id"]] = rows

    # Ozet — Crisis karsilastirma
    print(f"\n{B}{'='*60}{RESET}")
    print(f"{B}Ozet — Crisis (300ms / 30% kayip){RESET}")
    print(f"{B}{'='*60}{RESET}")
    crisis = all_results["scenarios"].get("crisis", [])
    if crisis:
        hdr = f"  {'Protokol':<16} {'Verim%':>7} {'Jitter':>8} {'Stall%':>7} {'p99ms':>8}"
        print(hdr)
        print(f"  {'-'*16} {'-'*7} {'-'*8} {'-'*7} {'-'*8}")
        for row in sorted(crisis, key=lambda x: -x["efficiency"]):
            c = G if "QDAP" in row["protocol"] else RESET
            print(
                f"  {c}{row['protocol']:<16}{RESET} "
                f"{row['efficiency']:>6.1f}% "
                f"{row['jitter_ms']:>7.1f}ms "
                f"{row['stall_rate']:>6.1f}% "
                f"{row['p99_ms']:>8.1f}"
            )

    out = RESULTS_DIR / "streaming_benchmark.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n{G}Sonuclar kaydedildi: {out}{RESET}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=float, default=TARGET_MBPS_DEFAULT,
                   help="Hedef bitrate Mbps (varsayilan: 5.0)")
    args = p.parse_args()
    asyncio.run(main(args.target))
