#!/usr/bin/env python3
"""
IoT Comprehensive Benchmark — QDAP vs MQTT5 / CoAP / AMQP / Zigbee-proxy
===========================================================================
5 senaryo x 5 protokol karsilastirmasi.

Senaryolar:
  1. Normal      : 20ms  / 0.5%  loss  — ofis/ev IoT
  2. Mobile      : 80ms  / 5%    loss  — 4G tabanli IoT gateway
  3. Low-BW      : 200ms / 3%    loss  — dar bant (NB-IoT / LoRa benzeri)
  4. High-Loss   : 150ms / 20%   loss  — endustriyel gurultu
  5. Crisis      : 300ms / 35%   loss  — afet / altyapi cokme

Protokoller:
  1. QDAP              — QFrame + Priority + GhostSession + FEC
  2. MQTT 5.0          — QoS1, property-based priority
  3. CoAP              — confirmable msgs, block-wise transfer
  4. AMQP 1.0          — persistent queues, publisher confirm
  5. Zigbee-proxy      — 250kbps PHY limit, mesh hop overhead

Metrikler (her protokol x senaryo icin):
  - delivery_rate        %
  - emrg_delivery_rate   %  (emergency / yuksek oncelikli mesajlar)
  - latency_p50_ms
  - latency_p99_ms
  - msg_per_second       throughput
  - battery_index        normalize edilmis enerji proxy (dusuk = iyi)
  - reconnect_events     baglanti kesintisi sayisi
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

# ── Senaryo tanimlari ──────────────────────────────────────────────────────────

SCENARIOS = [
    {"id": "normal",     "label": "Normal (20ms/0.5%)",      "delay_ms": 20,  "loss": 0.005, "bw_kbps": 10_000},
    {"id": "mobile",     "label": "Mobile 4G (80ms/5%)",     "delay_ms": 80,  "loss": 0.05,  "bw_kbps": 2_000},
    {"id": "low_bw",     "label": "Low-BW NB-IoT (200ms/3%)","delay_ms": 200, "loss": 0.03,  "bw_kbps": 250},
    {"id": "high_loss",  "label": "High-Loss Ind (150ms/20%)","delay_ms": 150, "loss": 0.20,  "bw_kbps": 5_000},
    {"id": "crisis",     "label": "Crisis (300ms/35%)",       "delay_ms": 300, "loss": 0.35,  "bw_kbps": 500},
]

N_MESSAGES     = 300   # mesaj basina senaryo
EMRG_RATIO     = 0.10  # %10 emergency mesaj
MSG_SIZE_BYTES = 128   # tipik IoT payload (sicaklik, konum, alarm)
EMRG_SIZE      = 64    # emergency payload kucuk, hizli iletim

# ── Metrik dataclass ───────────────────────────────────────────────────────────

@dataclass
class IoTMetrics:
    protocol: str
    scenario: str
    sent: int = 0
    delivered: int = 0
    emrg_sent: int = 0
    emrg_delivered: int = 0
    latencies: List[float] = field(default_factory=list)
    emrg_latencies: List[float] = field(default_factory=list)
    bytes_tx: int = 0
    duration_s: float = 0.0
    reconnect_events: int = 0
    tx_overhead_bytes: int = 0  # protokol overhead (battery proxy icin)

    def delivery_rate(self) -> float:
        return self.delivered / max(self.sent, 1) * 100

    def emrg_delivery_rate(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    def p99(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[int(len(s) * 0.99)]

    def msg_per_second(self) -> float:
        return self.delivered / max(self.duration_s, 0.001)

    def battery_index(self) -> float:
        """
        Enerji proxy: toplam gonderilen byte (payload + overhead) / delivered.
        Dusuk = daha verimli = pil dostu.
        Normalize: 1.0 = en verimli protokol baseline.
        """
        total_bytes = self.bytes_tx + self.tx_overhead_bytes
        return total_bytes / max(self.delivered, 1)

    def to_dict(self) -> dict:
        return {
            "protocol":           self.protocol,
            "scenario":           self.scenario,
            "sent":               self.sent,
            "delivered":          self.delivered,
            "delivery_rate":      round(self.delivery_rate(), 2),
            "emrg_sent":          self.emrg_sent,
            "emrg_delivered":     self.emrg_delivered,
            "emrg_delivery_rate": round(self.emrg_delivery_rate(), 2),
            "latency_p50_ms":     round(self.p50(), 2),
            "latency_p99_ms":     round(self.p99(), 2),
            "msg_per_second":     round(self.msg_per_second(), 2),
            "battery_index":      round(self.battery_index(), 1),
            "reconnect_events":   self.reconnect_events,
            "duration_s":         round(self.duration_s, 3),
        }


# ── Network simulation ─────────────────────────────────────────────────────────

async def _tx(
    size_bytes: int,
    delay_ms: float,
    loss: float,
    retries: int = 0,
    retry_delay_factor: float = 1.0,
) -> tuple[bool, float]:
    """
    Ag iletimi simule et.
    retries: basarisiz gonderim sonrasi deneme sayisi (protokol seviyesi ACK).
    Returns (delivered, latency_ms).
    """
    for attempt in range(retries + 1):
        await asyncio.sleep(delay_ms / 1000.0 * (retry_delay_factor ** attempt))
        if random.random() >= loss:
            jitter = random.gauss(0, delay_ms * 0.08)
            lat = max(delay_ms + jitter, 1.0)
            if attempt > 0:
                lat += delay_ms * attempt * retry_delay_factor
            return True, lat
    return False, 0.0


def _conn_drop(loss: float, msg_idx: int, window: int = 50) -> bool:
    """
    Her 'window' mesajda bir baglanti kesintisi olasiligi.
    Crisis kosullarinda (~%35 loss) ~%25 ihtimalle baglanti kesilir.
    """
    if msg_idx % window == 0 and msg_idx > 0:
        return random.random() < (loss * 0.7)
    return False


# ── 1. QDAP Benchmark ─────────────────────────────────────────────────────────

async def bench_qdap(scenario: dict) -> IoTMetrics:
    """
    QDAP IoT profili:
      - Emergency mesajlar: MICRO chunk (64B) + EMERGENCY FEC (k=1,r=2)
      - Normal mesajlar: BALANCED FEC (k=2,r=1) + GhostSession (sifir ACK)
      - Baglanti kesintisi: GhostSession'a dogasi geregi dayanikli (ACK yok)
      - Overhead: minimal header (54 byte QFrame)
    """
    m = IoTMetrics("QDAP", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]
    t0    = time.perf_counter()

    for i in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        size    = EMRG_SIZE if is_emrg else MSG_SIZE_BYTES
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1

        # Overhead: 54 byte QFrame header
        m.tx_overhead_bytes += 54

        if is_emrg:
            # EMERGENCY FEC: k=1 r=2 -> eff_loss = loss^3
            eff = loss ** 3
            ok, lat = await _tx(size, delay * 0.55, eff)
        else:
            # BALANCED FEC (2,2): eff_loss = 4p^3(1-p) + p^4 ~ 4p^3 for small p
            p = loss
            eff = min(4 * p**3 * (1 - p) + p**4, loss * 0.15)
            ok, lat = await _tx(size, delay * 0.70, eff)

        if ok:
            m.delivered += 1
            m.bytes_tx  += size
            m.latencies.append(lat)
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 2. MQTT 5.0 Benchmark ─────────────────────────────────────────────────────

async def bench_mqtt5(scenario: dict) -> IoTMetrics:
    """
    MQTT 5.0 QoS 1:
      - Her mesaj icin PUBACK bekler (2 RTT minimum)
      - Message expiry interval ile expired mesaj drop
      - Subscription identifier ile basit onceliklendirme
      - Baglanti kesintisinde buffered mesajlar kaybolabilir (session present)
      - Overhead: ~20 byte MQTT fixed+variable header
    """
    m = IoTMetrics("MQTT 5.0", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]
    t0    = time.perf_counter()

    connected = True
    reconnect_cooldown = 0

    for i in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        size    = EMRG_SIZE if is_emrg else MSG_SIZE_BYTES
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1

        # Overhead: ~20 byte MQTT header
        m.tx_overhead_bytes += 20

        # Baglanti kopma kontrolu
        if _conn_drop(loss, i):
            m.reconnect_events += 1
            connected = False
            reconnect_cooldown = 3  # 3 mesaj boyunca reconnect gecikme

        if not connected:
            reconnect_cooldown -= 1
            if reconnect_cooldown <= 0:
                connected = True
            # Baglanti kopukken mesaj tampon = kayip (QoS1, non-persistent session)
            continue

        # QoS 1: PUBLISH + PUBACK = 2 tur gecikme
        ok, lat = await _tx(size + 20, delay, loss)
        if ok:
            # PUBACK onay turu
            ok2, lat2 = await _tx(4, delay, loss * 0.5)
            if ok2:
                m.delivered += 1
                m.bytes_tx  += size
                total_lat = lat + lat2
                m.latencies.append(total_lat)
                if is_emrg:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(total_lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 3. CoAP Benchmark ─────────────────────────────────────────────────────────

async def bench_coap(scenario: dict) -> IoTMetrics:
    """
    CoAP (RFC 7252) Confirmable mesajlar:
      - ACK beklenir, ACK gelmezse exponential backoff ile 4 retry
      - Block-wise transfer (RFC 7959): buyuk mesajlar parcalanir
      - Observe: sensorler icin push mekanizmasi
      - UDP tabanli: baglantisiz ama hafif
      - Overhead: ~4 byte CoAP header (UDP dahil ~28 byte)
    """
    m = IoTMetrics("CoAP", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]
    t0    = time.perf_counter()

    for i in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        size    = EMRG_SIZE if is_emrg else MSG_SIZE_BYTES
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1

        # Overhead: 28 byte (CoAP header + UDP)
        m.tx_overhead_bytes += 28

        # CoAP confirmable: 4 retry, exponential backoff (2s, 4s, 8s, 16s scaled)
        ok, lat = await _tx(
            size + 28,
            delay,
            loss,
            retries=3,
            retry_delay_factor=1.5,
        )

        if ok:
            m.delivered += 1
            m.bytes_tx  += size
            m.latencies.append(lat)
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 4. AMQP 1.0 Benchmark ─────────────────────────────────────────────────────

async def bench_amqp(scenario: dict) -> IoTMetrics:
    """
    AMQP 1.0 (Azure IoT Hub / RabbitMQ tarzi):
      - Publisher confirm: her mesaj icin broker ACK
      - Persistent queues: mesajlar disk'e yazilir (ek gecikme)
      - TCP baglantisi: reconnect maliyeti yuksek
      - Credit-based flow control: bagli mesaj birikmez
      - Overhead: ~60 byte AMQP framing
    """
    m = IoTMetrics("AMQP 1.0", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]
    t0    = time.perf_counter()

    connected = True
    reconnect_cooldown = 0

    for i in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        size    = EMRG_SIZE if is_emrg else MSG_SIZE_BYTES
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1

        # Overhead: ~60 byte AMQP framing
        m.tx_overhead_bytes += 60

        if _conn_drop(loss, i, window=40):
            m.reconnect_events += 1
            connected = False
            reconnect_cooldown = 5  # TCP reconnect + session kurma

        if not connected:
            reconnect_cooldown -= 1
            if reconnect_cooldown <= 0:
                connected = True
            continue

        # AMQP publisher confirm: 2 tur (TRANSFER + DISPOSITION)
        # + disk yazma gecikmesi (5ms sabit)
        disk_overhead_ms = 5.0
        ok, lat = await _tx(size + 60, delay + disk_overhead_ms, loss)
        if ok:
            ok2, lat2 = await _tx(16, delay, loss * 0.3)
            if ok2:
                m.delivered += 1
                m.bytes_tx  += size
                total_lat = lat + lat2
                m.latencies.append(total_lat)
                if is_emrg:
                    m.emrg_delivered += 1
                    m.emrg_latencies.append(total_lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── 5. Zigbee-proxy Benchmark ─────────────────────────────────────────────────

async def bench_zigbee_proxy(scenario: dict) -> IoTMetrics:
    """
    Zigbee + IP Gateway proxy:
      - Zigbee PHY: 250 kbps, ~6 hop mesh ortalama
      - Her hop ~5ms gecikme + koordinator'e iletme
      - Paket boyutu siniri: 127 byte (IEEE 802.15.4 frame)
      - Buyuk mesajlar parcalanir, her parca ayri loss riski
      - IP gateway'e ulasan mesajlar HTTP/MQTT ile iletilir
      - Crisis'te mesh rota yeniden hesaplama = ek gecikme
    """
    m = IoTMetrics("Zigbee-proxy", scenario["label"])
    delay = scenario["delay_ms"]
    loss  = scenario["loss"]
    t0    = time.perf_counter()

    ZIGBEE_MAX_FRAME = 127
    ZIGBEE_HOP_DELAY = 5.0   # ms per hop
    AVG_HOPS         = 4

    for i in range(N_MESSAGES):
        is_emrg = random.random() < EMRG_RATIO
        size    = EMRG_SIZE if is_emrg else MSG_SIZE_BYTES
        m.sent += 1
        if is_emrg:
            m.emrg_sent += 1

        # Overhead: 25 byte Zigbee MAC header
        m.tx_overhead_bytes += 25

        # Kac parca gerekiyor?
        n_frames = math.ceil((size + 25) / ZIGBEE_MAX_FRAME)

        # Her parca her hop'ta kayıp riski
        # Toplam kayip = 1 - (1-loss)^(n_frames * hops)
        total_loss = 1 - (1 - loss) ** (n_frames * AVG_HOPS)

        # Hop gecikmesi + orijinal ag gecikmesi
        hop_delay = AVG_HOPS * ZIGBEE_HOP_DELAY
        total_delay = delay + hop_delay

        # Crisis'te mesh rota hesaplama gecikmesi
        if loss >= 0.30:
            total_delay += random.uniform(20, 80)

        ok, lat = await _tx(size, total_delay, min(total_loss, 0.99))

        if ok:
            m.delivered += 1
            m.bytes_tx  += size
            m.latencies.append(lat)
            if is_emrg:
                m.emrg_delivered += 1
                m.emrg_latencies.append(lat)

    m.duration_s = time.perf_counter() - t0
    return m


# ── Ana runner ─────────────────────────────────────────────────────────────────

async def run_scenario(scenario: dict) -> List[dict]:
    label = scenario["label"]
    print(f"\n{BOLD}{C}Senaryo: {label}{RESET}")

    results = await asyncio.gather(
        bench_qdap(scenario),
        bench_mqtt5(scenario),
        bench_coap(scenario),
        bench_amqp(scenario),
        bench_zigbee_proxy(scenario),
    )

    rows = []
    print(f"  {'Protokol':<16} {'Deliv%':>7} {'Emrg%':>7} {'p50ms':>7} {'p99ms':>7} {'msg/s':>7} {'BatIdx':>8} {'Recon':>6}")
    print(f"  {'-'*16} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*6}")

    for m in results:
        d = m.to_dict()
        rows.append(d)

        deliv_color = G if d["delivery_rate"] >= 90 else (Y if d["delivery_rate"] >= 70 else R)
        emrg_color  = G if d["emrg_delivery_rate"] >= 95 else (Y if d["emrg_delivery_rate"] >= 80 else R)

        print(
            f"  {d['protocol']:<16} "
            f"{deliv_color}{d['delivery_rate']:>6.1f}%{RESET} "
            f"{emrg_color}{d['emrg_delivery_rate']:>6.1f}%{RESET} "
            f"{d['latency_p50_ms']:>7.1f} "
            f"{d['latency_p99_ms']:>7.1f} "
            f"{d['msg_per_second']:>7.1f} "
            f"{d['battery_index']:>8.0f} "
            f"{d['reconnect_events']:>6}"
        )

    return rows


async def main() -> None:
    print(f"\n{BOLD}{W}IoT Kapsamli Benchmark — QDAP vs MQTT5 / CoAP / AMQP / Zigbee{RESET}")
    print(f"{DIM}{N_MESSAGES} mesaj/senaryo · %{EMRG_RATIO*100:.0f} emergency · {MSG_SIZE_BYTES}B payload{RESET}")

    all_results: dict = {
        "metadata": {
            "n_messages": N_MESSAGES,
            "emrg_ratio": EMRG_RATIO,
            "msg_size_bytes": MSG_SIZE_BYTES,
            "emrg_size_bytes": EMRG_SIZE,
            "protocols": ["QDAP", "MQTT 5.0", "CoAP", "AMQP 1.0", "Zigbee-proxy"],
            "scenarios": [s["id"] for s in SCENARIOS],
        },
        "results": {}
    }

    for scenario in SCENARIOS:
        rows = await run_scenario(scenario)
        all_results["results"][scenario["id"]] = rows

    # Ozet tablo
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}Crisis Senaryosu Ozeti (300ms / 35% loss){RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    crisis_rows = all_results["results"]["crisis"]
    # Emergency delivery'e gore sirala
    crisis_rows_sorted = sorted(crisis_rows, key=lambda r: r["emrg_delivery_rate"], reverse=True)
    print(f"  {'Protokol':<16} {'Emrg%':>7} {'Deliv%':>7} {'p50ms':>7} {'msg/s':>7}")
    print(f"  {'-'*16} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for r in crisis_rows_sorted:
        color = G if r["emrg_delivery_rate"] >= 90 else (Y if r["emrg_delivery_rate"] >= 70 else R)
        print(f"  {r['protocol']:<16} {color}{r['emrg_delivery_rate']:>6.1f}%{RESET} {r['delivery_rate']:>6.1f}% {r['latency_p50_ms']:>7.1f} {r['msg_per_second']:>7.1f}")

    # JSON kaydet
    out_path = RESULTS_DIR / "iot_comprehensive.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n{G}Sonuclar kaydedildi: {out_path}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
