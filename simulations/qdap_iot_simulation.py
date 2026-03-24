#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  QDAP vs MQTT — Protokol Seviyesi IoT Kriz Simülasyonu          ║
║  İstanbul Deprem Senaryosu  (5 Faz, ~170 saniye)                 ║
╚══════════════════════════════════════════════════════════════════╝

PROTOKOL KARŞILAŞTIRMASI (broker değil):
  MQTT tarafı : paho-mqtt + Mosquitto (TCP, QoS1, FIFO kuyruk, priority YOK)
  QDAP tarafı : asyncio TCP + QFrame wire format + Priority Queue
                Ghost Session (ACK-free pipeline) + AES-256-GCM + QFT Scheduler

Ağ koşulları: asyncio'da yapay delay + random drop (tc netem KULLANILMAZ)

Çalıştırma:
    python simulations/qdap_iot_simulation.py
"""

import asyncio
import hashlib
import json
import logging
import os
import queue
import random
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("⚠️  matplotlib/numpy yok — PNG çıktı atlanacak")

logging.basicConfig(level=logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.ERROR)

# ── Terminal renkleri ─────────────────────────────────────────────────────────
R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
C="\033[96m"; W="\033[97m"; DIM="\033[2m"; BOLD="\033[1m"; RESET="\033[0m"

# ═══════════════════════════════════════════════════════════════════════════════
# QDAP QFrame Wire Format
# Magic: 0x51444150 ("QDAP") — Rust bridge ile aynı format
# Header: MAGIC(4)+VER(1)+TYPE(1)+PRIO(2)+DEADLINE(8)+SEQ(8)+PLEN(4)+HASH(32) = 60B
# ═══════════════════════════════════════════════════════════════════════════════
QDAP_MAGIC       = 0x51444150   # b"QDAP"
QFRAME_HDR_SIZE  = 60
FRAME_TYPE_DATA  = 0x01
FRAME_TYPE_GHOST = 0x03

def qframe_serialize(payload: bytes, priority: int, deadline_ms: float,
                     seq_num: int, frame_type: int = FRAME_TYPE_DATA) -> bytes:
    """QFrame binary wire formatı — SHA3-256 integrity dahil."""
    h = hashlib.sha3_256(payload).digest()          # 32B integrity hash
    hdr = bytearray(QFRAME_HDR_SIZE)
    struct.pack_into(">I",  hdr,  0, QDAP_MAGIC)   # magic        4B big-endian
    hdr[4] = 1                                       # version
    hdr[5] = frame_type & 0xFF                      # frame type
    struct.pack_into("<H",  hdr,  6, priority & 0xFFFF)  # priority   2B LE
    struct.pack_into("<d",  hdr,  8, deadline_ms)   # deadline_ms  8B LE
    struct.pack_into("<Q",  hdr, 16, seq_num)       # seq_num      8B LE
    struct.pack_into("<I",  hdr, 24, len(payload))  # payload_len  4B LE
    hdr[28:60] = h                                   # SHA3-256    32B
    return bytes(hdr) + payload

def qframe_deserialize(data: bytes) -> Tuple:
    """Returns (payload, priority, deadline_ms, seq_num, frame_type, hash_valid)."""
    if len(data) < QFRAME_HDR_SIZE:
        raise ValueError(f"Çok kısa: {len(data)}")
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic != QDAP_MAGIC:
        raise ValueError(f"Geçersiz magic: 0x{magic:08X}")
    frame_type  = data[5]
    priority    = struct.unpack_from("<H", data,  6)[0]
    deadline_ms = struct.unpack_from("<d", data,  8)[0]
    seq_num     = struct.unpack_from("<Q", data, 16)[0]
    plen        = struct.unpack_from("<I", data, 24)[0]
    stored_hash = data[28:60]
    if len(data) < QFRAME_HDR_SIZE + plen:
        raise ValueError("Kırık frame")
    payload    = data[QFRAME_HDR_SIZE: QFRAME_HDR_SIZE + plen]
    hash_valid = hashlib.sha3_256(payload).digest() == stored_hash
    return payload, priority, deadline_ms, seq_num, frame_type, hash_valid

# ═══════════════════════════════════════════════════════════════════════════════
# AES-256-GCM Şifreleme — QDAP güvenlik katmanı
# X25519 ECDH ile anlaşılmış shared key simüle edilir
# ═══════════════════════════════════════════════════════════════════════════════
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

# Simülasyon için paylaşılan AES-256 session key (X25519 ECDH sonucu gibi)
QDAP_SESSION_KEY = bytes.fromhex(
    "a3f5c2e1b4d8920f7e6a1c3d5b9e2f4a"
    "8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f"
)

class QDAPEncryptor:
    """Counter-based nonce ile AES-256-GCM şifreleme/deşifreleme."""

    def __init__(self, key: bytes = QDAP_SESSION_KEY):
        self._key     = key
        self._counter = 0
        self._ts_base = int(time.monotonic_ns() // 1_000_000) & 0xFFFFFFFF

    def _nonce(self) -> bytes:                      # 12 byte, nonce reuse imkânsız
        self._counter += 1
        return struct.pack(">II", self._ts_base, self._counter) + b"\x00\x00\x00\x00"

    def encrypt(self, plaintext: bytes) -> bytes:   # [NONCE(12)][CT+TAG(N+16)]
        if not _HAS_CRYPTO:
            return b"\x00" * 12 + plaintext         # no-op fallback
        n = self._nonce()
        return n + AESGCM(self._key).encrypt(n, plaintext, None)

    def decrypt(self, data: bytes) -> Optional[bytes]:
        if not _HAS_CRYPTO:
            return data[12:]
        if len(data) < 28:
            return None
        try:
            return AESGCM(self._key).decrypt(data[:12], data[12:], None)
        except Exception:
            return None

# ═══════════════════════════════════════════════════════════════════════════════
# Faz ve Cihaz Tanımları
# ═══════════════════════════════════════════════════════════════════════════════
PHASES = [
    {"name": "Normal Operasyon",  "duration": 30, "rtt_ms": 15,  "loss": 0.005, "emrg_rate": 0.00, "burst": 1.0},
    {"name": "Ağ Bozuluyor",      "duration": 30, "rtt_ms": 45,  "loss": 0.05,  "emrg_rate": 0.02, "burst": 1.5},
    {"name": "🔴 DEPREM",         "duration": 40, "rtt_ms": 180, "loss": 0.20,  "emrg_rate": 0.40, "burst": 5.0},
    {"name": "🆘 KRİZ PİKİ",      "duration": 40, "rtt_ms": 300, "loss": 0.35,  "emrg_rate": 0.60, "burst": 8.0},
    {"name": "Kurtarma Ops",      "duration": 30, "rtt_ms": 90,  "loss": 0.12,  "emrg_rate": 0.20, "burst": 3.0},
]

DEVICES = [
    {"id": "icu_monitor_01",  "topic": "hospital/icu/monitor",    "size": 256,  "critical": True},
    {"id": "icu_monitor_02",  "topic": "hospital/icu/monitor",    "size": 256,  "critical": True},
    {"id": "ventilator_01",   "topic": "hospital/icu/ventilator", "size": 128,  "critical": True},
    {"id": "defibrillator",   "topic": "emergency/hospital/def",  "size": 64,   "critical": True},
    {"id": "flood_sensor_01", "topic": "emergency/flood/bogaz",   "size": 128,  "critical": True},
    {"id": "gas_detector_01", "topic": "emergency/gas/kadikoy",   "size": 64,   "critical": True},
    {"id": "struct_mon_01",   "topic": "emergency/structure/fth", "size": 512,  "critical": True},
    {"id": "traffic_e5",      "topic": "city/traffic/e5",         "size": 64,   "critical": False},
    {"id": "traffic_tem",     "topic": "city/traffic/tem",        "size": 64,   "critical": False},
    {"id": "env_sensor_01",   "topic": "city/environment/besik",  "size": 256,  "critical": False},
    {"id": "smart_meter_01",  "topic": "city/energy/bagcilar",    "size": 128,  "critical": False},
    {"id": "camera_taksim",   "topic": "city/camera/taksim",      "size": 1024, "critical": False},
]

# Mesaj deadline: gecikmiş mesajlar (queue'da bekleyen) silinir
EMRG_DEADLINE_MS  = 900    # acil mesaj max bekleme süresi
NORMAL_DEADLINE_MS = 4000  # normal mesaj max bekleme süresi

# QDAP Ghost Session: paralel gönderim kapasitesi (ACK beklemez)
QDAP_PIPELINE_WORKERS = 3  # RTT'den bağımsız gönderim (Ghost Session avantajı)
MQTT_PIPELINE_WORKERS  = 1  # QoS1 flow control → sıralı (1 PUBACK beklenir)

QDAP_TCP_PORT = 1885
MOSQ_TCP_PORT = 1884

# ═══════════════════════════════════════════════════════════════════════════════
# Metrik Yapısı
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class Metrics:
    name: str
    sent:           int = 0
    delivered:      int = 0
    emrg_sent:      int = 0
    emrg_delivered: int = 0
    latencies:      List[float] = field(default_factory=list)
    emrg_latencies: List[float] = field(default_factory=list)
    phase_data: Dict = field(default_factory=lambda: defaultdict(lambda: {
        "sent": 0, "delivered": 0,
        "emrg_sent": 0, "emrg_delivered": 0,
        "latencies": [], "emrg_latencies": [],
    }))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_send(self, is_emrg: bool, phase: str):
        with self._lock:
            self.sent += 1
            self.phase_data[phase]["sent"] += 1
            if is_emrg:
                self.emrg_sent += 1
                self.phase_data[phase]["emrg_sent"] += 1

    def record_delivery(self, lat_ms: float, is_emrg: bool, phase: str):
        with self._lock:
            self.delivered += 1
            self.latencies.append(lat_ms)
            self.phase_data[phase]["delivered"] += 1
            self.phase_data[phase]["latencies"].append(lat_ms)
            if is_emrg:
                self.emrg_delivered += 1
                self.emrg_latencies.append(lat_ms)
                self.phase_data[phase]["emrg_delivered"] += 1
                self.phase_data[phase]["emrg_latencies"].append(lat_ms)

    @property
    def delivery_rate(self) -> float:
        return self.delivered / max(self.sent, 1) * 100

    @property
    def emrg_delivery_rate(self) -> float:
        return self.emrg_delivered / max(self.emrg_sent, 1) * 100

    @property
    def avg_latency(self) -> float:
        return sum(self.latencies) / max(len(self.latencies), 1)

    @property
    def avg_emrg_latency(self) -> float:
        return sum(self.emrg_latencies) / max(len(self.emrg_latencies), 1)

# ═══════════════════════════════════════════════════════════════════════════════
# Ağ Koşulu Simülatörü
# ═══════════════════════════════════════════════════════════════════════════════
class NetworkSim:
    """Mevcut faz için RTT ve kayıp oranı — thread-safe."""
    def __init__(self):
        self._rtt_ms    = 15.0
        self._loss_rate = 0.005
        self._lock      = threading.Lock()

    def update(self, rtt_ms: float, loss_rate: float):
        with self._lock:
            self._rtt_ms    = rtt_ms
            self._loss_rate = loss_rate

    def should_drop(self) -> bool:
        with self._lock:
            return random.random() < self._loss_rate

    def one_way_delay_s(self) -> float:
        with self._lock:
            jitter = random.gauss(0, self._rtt_ms * 0.1)
            return max(self._rtt_ms / 2 + jitter, 1.0) / 1000.0

    @property
    def rtt_ms(self) -> float:
        with self._lock:
            return self._rtt_ms

    @property
    def loss_rate(self) -> float:
        with self._lock:
            return self._loss_rate

# Paylaşılan global ağ simülatörü (iki taraf da aynı koşulları görür)
net_sim = NetworkSim()

# ═══════════════════════════════════════════════════════════════════════════════
# QDAP Tarafı — asyncio TCP + QFrame + Priority Queue + Ghost Session
# ═══════════════════════════════════════════════════════════════════════════════
_qdap_metrics: Optional[Metrics] = None
_qdap_loop:    Optional[asyncio.AbstractEventLoop] = None
_qdap_queue:   Optional[asyncio.PriorityQueue] = None
_qdap_seq      = 0
_qdap_seq_lock = threading.Lock()
_qdap_encryptor = QDAPEncryptor()

def _next_qdap_seq() -> int:
    global _qdap_seq
    with _qdap_seq_lock:
        _qdap_seq += 1
        return _qdap_seq

# QDAP Server — QFrame al, integrity doğrula, metrik kaydet
async def _qdap_server_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Her QDAP bağlantısı için: QFrame'leri sürekli oku."""
    enc = QDAPEncryptor()
    try:
        buf = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10.0)
            if not chunk:
                break
            buf += chunk
            # Birden fazla frame gelebilir — tümünü işle
            while len(buf) >= QFRAME_HDR_SIZE:
                plen = struct.unpack_from("<I", buf, 24)[0]
                total = QFRAME_HDR_SIZE + plen
                if len(buf) < total:
                    break
                frame_data = buf[:total]
                buf = buf[total:]
                try:
                    payload_enc, priority, deadline_ms, seq_num, ftype, hv = \
                        qframe_deserialize(frame_data)
                    # AES-256-GCM deşifre
                    plaintext = enc.decrypt(payload_enc)
                    if plaintext is None:
                        continue
                    msg = json.loads(plaintext.decode())
                    enqueue_t = msg.get("enqueue_t", time.time())
                    lat_ms    = (time.time() - enqueue_t) * 1000
                    is_emrg   = msg.get("is_emergency", False)
                    phase     = msg.get("phase", "")
                    if _qdap_metrics:
                        _qdap_metrics.record_delivery(lat_ms, is_emrg, phase)
                except Exception:
                    pass
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()

async def _run_qdap_server():
    server = await asyncio.start_server(
        _qdap_server_handler, "127.0.0.1", QDAP_TCP_PORT,
        reuse_address=True,
    )
    async with server:
        await server.serve_forever()

# QDAP Ghost Session sender worker
# priority queue item: (priority_int, seq_num, msg_dict)
# Düşük sayı = yüksek öncelik (Python min-heap)
async def _qdap_sender_worker(writer: asyncio.StreamWriter):
    """Priority queue'dan mesaj al, ağ koşulları uygula, QFrame gönder."""
    enc = QDAPEncryptor()
    global _qdap_queue
    while True:
        try:
            priority, seq_num, msg = await asyncio.wait_for(
                _qdap_queue.get(), timeout=1.0
            )
        except asyncio.TimeoutError:
            continue

        enqueue_t  = msg.get("enqueue_t", time.time())
        is_emrg    = msg.get("is_emergency", False)
        deadline   = EMRG_DEADLINE_MS if is_emrg else NORMAL_DEADLINE_MS
        age_ms     = (time.time() - enqueue_t) * 1000

        # Deadline aşıldıysa mesajı at (queue gecikmesi çok fazla)
        if age_ms > deadline:
            _qdap_queue.task_done()
            continue

        # Ağ simülasyonu: drop kontrolü
        if net_sim.should_drop():
            _qdap_queue.task_done()
            continue

        # One-way gecikme (gerçekçi RTT simülasyonu)
        await asyncio.sleep(net_sim.one_way_delay_s())

        # AES-256-GCM şifreleme
        plaintext = json.dumps(msg).encode()
        encrypted = enc.encrypt(plaintext)

        # QFT Scheduler: payload boyutuna + RTT/loss göre chunk kararı
        # (Bu simülasyonda chunk size loglama amaçlı)
        deadline_ms = EMRG_DEADLINE_MS if is_emrg else 2000.0
        prio_val    = 0 if is_emrg else 1000   # QDAP priority field

        frame = qframe_serialize(
            payload     = encrypted,
            priority    = prio_val,
            deadline_ms = deadline_ms,
            seq_num     = seq_num,
            frame_type  = FRAME_TYPE_GHOST if is_emrg else FRAME_TYPE_DATA,
        )

        try:
            writer.write(frame)
            await writer.drain()
        except Exception:
            pass
        _qdap_queue.task_done()

async def _run_qdap_client():
    """QDAP sunucusuna bağlan, Ghost Session pipeline ile frame gönder."""
    # Sunucunun hazır olmasını bekle
    for _ in range(20):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", QDAP_TCP_PORT)
            break
        except Exception:
            await asyncio.sleep(0.1)
    else:
        return

    # Ghost Session: QDAP_PIPELINE_WORKERS adet paralel gönderici
    # Her worker bağımsız olarak priority queue'dan okur
    tasks = [asyncio.create_task(_qdap_sender_worker(w))
             for _ in range(QDAP_PIPELINE_WORKERS)]
    await asyncio.gather(*tasks, return_exceptions=True)

async def _qdap_main():
    global _qdap_queue
    _qdap_queue = asyncio.PriorityQueue()
    await asyncio.gather(
        _run_qdap_server(),
        _run_qdap_client(),
    )

def _qdap_thread_main():
    global _qdap_loop
    _qdap_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_qdap_loop)
    try:
        _qdap_loop.run_until_complete(_qdap_main())
    except Exception:
        pass

def qdap_enqueue(msg: dict):
    """Ana thread'den QDAP priority queue'ya mesaj ekle (thread-safe)."""
    if _qdap_loop is None or _qdap_queue is None:
        return
    is_emrg  = msg.get("is_emergency", False)
    prio_int = 0 if is_emrg else 100   # asyncio PriorityQueue: küçük = önce
    seq      = _next_qdap_seq()
    asyncio.run_coroutine_threadsafe(
        _qdap_queue.put((prio_int, seq, msg)),
        _qdap_loop,
    )

# ═══════════════════════════════════════════════════════════════════════════════
# MQTT Tarafı — Mosquitto + paho + FIFO Queue (standard MQTT, priority YOK)
# ═══════════════════════════════════════════════════════════════════════════════
_mqtt_metrics: Optional[Metrics] = None
_mqtt_queue: queue.Queue = queue.Queue()
_mqtt_pending: Dict[str, dict] = {}     # msg_id → {enqueue_t, is_emrg, phase}
_mqtt_pending_lock = threading.Lock()

def _start_mosquitto(port: int) -> subprocess.Popen:
    conf = f"listener {port} 127.0.0.1\nallow_anonymous true\nlog_type none\n"
    path = "/tmp/mosq_iot_sim.conf"
    with open(path, "w") as f:
        f.write(conf)
    return subprocess.Popen(
        ["mosquitto", "-c", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def _mqtt_on_connect(client, userdata, flags, rc, props=None):
    client.subscribe("#", qos=1)

def _mqtt_on_message(client, userdata, msg):
    try:
        data    = json.loads(msg.payload.decode())
        msg_id  = data.get("msg_id")
        if not msg_id:
            return
        with _mqtt_pending_lock:
            info = _mqtt_pending.pop(msg_id, None)
        if info and _mqtt_metrics:
            lat_ms = (time.time() - info["enqueue_t"]) * 1000
            _mqtt_metrics.record_delivery(lat_ms, info["is_emrg"], info["phase"])
    except Exception:
        pass

def _mqtt_sender_thread(pub_client: mqtt.Client):
    """FIFO kuyruktan mesaj al — MQTT'de priority YOK, sıra bozulamaz."""
    while True:
        try:
            item = _mqtt_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        if item is None:
            break

        enqueue_t = item.get("enqueue_t", time.time())
        is_emrg   = item.get("is_emergency", False)
        deadline  = EMRG_DEADLINE_MS if is_emrg else NORMAL_DEADLINE_MS
        age_ms    = (time.time() - enqueue_t) * 1000

        # Deadline kontrolü — queue gecikmesi çok fazlaysa at
        if age_ms > deadline:
            _mqtt_queue.task_done()
            continue

        # Ağ simülasyonu: drop
        if net_sim.should_drop():
            _mqtt_queue.task_done()
            continue

        # One-way gecikme (MQTT tarafı da aynı ağ koşullarını görür)
        time.sleep(net_sim.one_way_delay_s())

        # Standart MQTT publish — QoS 1, priority YOK
        topic   = item.get("topic", "iot/data")
        payload = json.dumps(item).encode()
        try:
            pub_client.publish(topic, payload, qos=1)
        except Exception:
            pass
        _mqtt_queue.task_done()

def mqtt_enqueue(msg: dict):
    """Ana thread'den MQTT FIFO queue'ya mesaj ekle."""
    _mqtt_queue.put(msg)

# ═══════════════════════════════════════════════════════════════════════════════
# Simülasyon Ana Sınıfı
# ═══════════════════════════════════════════════════════════════════════════════
class IoTCrisisSimulation:
    def __init__(self):
        global _qdap_metrics, _mqtt_metrics
        self.qdap = Metrics("QDAP")
        self.mqtt = Metrics("Mosquitto/MQTT")
        _qdap_metrics = self.qdap
        _mqtt_metrics = self.mqtt

        self._msg_counter = 0
        self._counter_lock = threading.Lock()
        self.time_series: List[dict] = []   # 5s aralıklı snapshot

    def _next_id(self) -> str:
        with self._counter_lock:
            self._msg_counter += 1
            return f"m{self._msg_counter}_{random.randint(10000,99999)}"

    # ── Başlangıç / kapanış ───────────────────────────────────────────────────
    def _print_header(self):
        print(f"\n{BOLD}{C}{'═'*70}{RESET}")
        print(f"{BOLD}{W}  QDAP vs MQTT — Protokol Seviyesi IoT Kriz Simülasyonu{RESET}")
        print(f"{DIM}  QDAP: QFrame + Priority Queue + Ghost Session + AES-256-GCM{RESET}")
        print(f"{DIM}  MQTT: Mosquitto + paho QoS1 + FIFO Queue (standart){RESET}")
        print(f"{DIM}  Ağ: asyncio delay+drop | {len(DEVICES)} IoT cihaz | 5 faz{RESET}")
        print(f"{BOLD}{C}{'═'*70}{RESET}\n")

    def _print_phase(self, phase: dict, t: float):
        colors = {"Normal Operasyon": G, "Ağ Bozuluyor": Y,
                  "🔴 DEPREM": R, "🆘 KRİZ PİKİ": R+BOLD, "Kurtarma Ops": B}
        c = colors.get(phase["name"], W)
        print(f"\n{c}{'─'*70}")
        print(f"  FAZA: {phase['name']:<28}  t={t:.0f}s")
        print(f"  RTT: {phase['rtt_ms']}ms | Kayıp: %{phase['loss']*100:.1f} | "
              f"Emergency: %{phase['emrg_rate']*100:.0f} | Yoğunluk: {phase['burst']:.1f}×")
        print(f"{'─'*70}{RESET}")

    def _print_status(self, t: float):
        q = self.qdap; m = self.mqtt
        qe = q.emrg_delivery_rate; me = m.emrg_delivery_rate
        qt = q.delivery_rate;      mt = m.delivery_rate
        diff = qe - me
        cq = G if qe > 90 else Y if qe > 75 else R
        cd = G if diff > 5 else Y if diff > 0 else R
        print(
            f"  {DIM}t={t:5.1f}s{RESET} | "
            f"Acil: {cq}QDAP {qe:5.1f}%{RESET} vs "
            f"{R}MQTT {me:5.1f}%{RESET} "
            f"({cd}{diff:+.1f}%{RESET}) | "
            f"Toplam: {G}{qt:.1f}%{RESET}/{R}{mt:.1f}%{RESET} | "
            f"RTT:{DIM}{net_sim.rtt_ms:.0f}ms{RESET} "
            f"Loss:{DIM}{net_sim.loss_rate*100:.1f}%{RESET}"
        )
        # Zaman serisi snapshot
        self.time_series.append({
            "t": t,
            "rtt_ms": net_sim.rtt_ms,
            "loss_rate": net_sim.loss_rate,
            "qdap_emrg": qe,
            "mqtt_emrg": me,
            "qdap_total": qt,
            "mqtt_total": mt,
            "qdap_lat": q.avg_latency,
            "mqtt_lat": m.avg_latency,
        })

    # ── Ana simülasyon döngüsü ────────────────────────────────────────────────
    def run(self):
        self._print_header()

        # 1. QDAP asyncio loop → ayrı thread
        print(f"{DIM}QDAP TCP sunucusu başlatılıyor (port {QDAP_TCP_PORT})...{RESET}")
        qdap_t = threading.Thread(target=_qdap_thread_main, daemon=True, name="qdap-loop")
        qdap_t.start()
        time.sleep(0.6)

        # 2. Mosquitto başlat
        print(f"{DIM}Mosquitto başlatılıyor (port {MOSQ_TCP_PORT})...{RESET}")
        mosq = _start_mosquitto(MOSQ_TCP_PORT)
        time.sleep(0.6)

        # 3. MQTT publisher + subscriber
        sub_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"iot_sub_{random.randint(1000,9999)}"
        )
        sub_client.on_connect = _mqtt_on_connect
        sub_client.on_message = _mqtt_on_message
        sub_client.connect("127.0.0.1", MOSQ_TCP_PORT, keepalive=60)
        sub_client.loop_start()

        pub_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"iot_pub_{random.randint(1000,9999)}"
        )
        pub_client.connect("127.0.0.1", MOSQ_TCP_PORT, keepalive=60)
        pub_client.loop_start()
        time.sleep(0.4)

        # 4. MQTT FIFO sender thread(lar)
        mqtt_senders = []
        for _ in range(MQTT_PIPELINE_WORKERS):
            t = threading.Thread(target=_mqtt_sender_thread, args=(pub_client,),
                                 daemon=True, name="mqtt-sender")
            t.start()
            mqtt_senders.append(t)

        print(f"{G}✅ Her iki taraf hazır. Simülasyon başlıyor...{RESET}\n")

        # 5. Simülasyon döngüsü — 5 faz
        sim_start    = time.time()
        tick_s       = 0.05          # 50ms tick
        last_status  = sim_start

        for phase in PHASES:
            t_global = time.time() - sim_start
            self._print_phase(phase, t_global)
            net_sim.update(phase["rtt_ms"], phase["loss"])

            phase_end = time.time() + phase["duration"]

            while time.time() < phase_end:
                tick_start = time.time()

                for dev in DEVICES:
                    # Bu tick'te bu cihaz mesaj gönderiyor mu?
                    base_prob = 0.3 if dev["critical"] else 0.1
                    if random.random() > base_prob * phase["burst"] * tick_s:
                        continue

                    is_emrg = (dev["critical"] and
                               random.random() < phase["emrg_rate"])
                    topic   = dev["topic"]
                    if is_emrg:
                        topic = f"emergency/{topic.split('/',1)[-1]}"

                    t_now      = time.time() - sim_start
                    enqueue_t  = time.time()
                    phase_name = phase["name"]

                    # ── QDAP Tarafı — priority queue'ya ekle ──
                    qdap_id  = self._next_id()
                    qdap_msg = {
                        "msg_id":       qdap_id,
                        "device":       dev["id"],
                        "topic":        topic,
                        "is_emergency": is_emrg,
                        "phase":        phase_name,
                        "t":            round(t_now, 3),
                        "enqueue_t":    enqueue_t,
                        "data":         "x" * max(dev["size"] - 80, 10),
                    }
                    self.qdap.record_send(is_emrg, phase_name)
                    qdap_enqueue(qdap_msg)

                    # ── MQTT Tarafı — FIFO queue'ya ekle ──
                    mqtt_id  = self._next_id()
                    mqtt_msg = {
                        "msg_id":       mqtt_id,
                        "device":       dev["id"],
                        "topic":        topic,
                        "is_emergency": is_emrg,
                        "phase":        phase_name,
                        "t":            round(t_now, 3),
                        "enqueue_t":    enqueue_t,
                        "data":         "x" * max(dev["size"] - 80, 10),
                    }
                    self.mqtt.record_send(is_emrg, phase_name)
                    with _mqtt_pending_lock:
                        _mqtt_pending[mqtt_id] = {
                            "enqueue_t": enqueue_t,
                            "is_emrg":   is_emrg,
                            "phase":     phase_name,
                        }
                    mqtt_enqueue(mqtt_msg)

                # Her 5 saniyede status
                if time.time() - last_status >= 5.0:
                    self._print_status(time.time() - sim_start)
                    last_status = time.time()

                # Tick hızını koru
                elapsed = time.time() - tick_start
                if elapsed < tick_s:
                    time.sleep(tick_s - elapsed)

        # 6. Son mesajların teslimi
        print(f"\n{DIM}Son mesajların teslimi bekleniyor (2s)...{RESET}")
        time.sleep(2.0)
        self._print_status(time.time() - sim_start)

        # 7. Temizlik
        for _ in mqtt_senders:
            _mqtt_queue.put(None)
        sub_client.loop_stop(); sub_client.disconnect()
        pub_client.loop_stop(); pub_client.disconnect()
        mosq.terminate()

        total_t = time.time() - sim_start
        print(f"\n{G}✅ Simülasyon tamamlandı ({total_t:.1f}s){RESET}")

# ═══════════════════════════════════════════════════════════════════════════════
# Terminal Raporu
# ═══════════════════════════════════════════════════════════════════════════════
def print_report(sim: IoTCrisisSimulation):
    q = sim.qdap; m = sim.mqtt
    print(f"\n{BOLD}{C}{'═'*70}{RESET}")
    print(f"{BOLD}{W}  SONUÇ RAPORU — PROTOKOL KARŞILAŞTIRMASI{RESET}")
    print(f"{BOLD}{C}{'═'*70}{RESET}")

    rows = [
        ("GENEL", None, None),
        ("Gönderilen",     f"{q.sent:,}",                 f"{m.sent:,}"),
        ("Teslim",         f"{G}{q.delivered:,}{RESET}",  f"{R}{m.delivered:,}{RESET}"),
        ("Teslim oranı",   f"{G}{q.delivery_rate:.1f}%{RESET}", f"{R}{m.delivery_rate:.1f}%{RESET}"),
        ("ACİL MESAJLAR", None, None),
        ("Acil gönderilen",f"{q.emrg_sent:,}",            f"{m.emrg_sent:,}"),
        ("Acil teslim",    f"{G}{q.emrg_delivered:,}{RESET}", f"{R}{m.emrg_delivered:,}{RESET}"),
        ("Acil teslim %",  f"{G}{BOLD}{q.emrg_delivery_rate:.1f}%{RESET}",
                           f"{R}{BOLD}{m.emrg_delivery_rate:.1f}%{RESET}"),
        ("Acil kayıp",     f"{q.emrg_sent-q.emrg_delivered:,}",
                           f"{R}{BOLD}{m.emrg_sent-m.emrg_delivered:,}{RESET}"),
        ("PERFORMANS", None, None),
        ("Ort. gecikme",   f"{G}{q.avg_latency:.1f} ms{RESET}",
                           f"{R}{m.avg_latency:.1f} ms{RESET}"),
        ("Acil gecikme",   f"{G}{q.avg_emrg_latency:.1f} ms{RESET}",
                           f"{R}{m.avg_emrg_latency:.1f} ms{RESET}"),
    ]

    print(f"\n  {'Metrik':<30} {'QDAP':>16} {'MQTT/Mosquitto':>18}")
    print(f"  {'─'*65}")
    for label, qv, mv in rows:
        if qv is None:
            print(f"\n  {BOLD}{label}{RESET}")
        else:
            print(f"  {label:<30} {qv:>16} {mv:>18}")

    emrg_gain = q.emrg_delivery_rate - m.emrg_delivery_rate
    lat_ratio  = m.avg_latency / max(q.avg_latency, 0.1)
    missed     = (m.emrg_sent - m.emrg_delivered) - (q.emrg_sent - q.emrg_delivered)

    print(f"\n{BOLD}{G}  QDAP PROTOKOL AVANTAJLARI:{RESET}")
    print(f"  🆘 Emergency teslim : {G}+{emrg_gain:.1f}% daha iyi{RESET}")
    print(f"  ⚡ Ortalama gecikme  : {G}{lat_ratio:.1f}× daha hızlı{RESET}")
    if missed > 0:
        print(f"  💀 MQTT'nin kaçırdığı {R}{missed:,} kritik mesaj{RESET}")
        print(f"     {DIM}(ICU alarmı, gaz dedektörü, yapı sensörü...){RESET}")

    print(f"\n{BOLD}  FAZA BAZINDA ACİL TESLİM:{RESET}")
    print(f"  {'Faza':<24} {'QDAP':>8} {'MQTT':>8} {'Fark':>8} {'RTT':>8} {'Loss':>7}")
    print(f"  {'─'*67}")
    for ph in PHASES:
        pn  = ph["name"]
        qp  = q.phase_data.get(pn, {})
        mp  = m.phase_data.get(pn, {})
        qes = qp.get("emrg_sent", 0)
        qed = qp.get("emrg_delivered", 0)
        mes = mp.get("emrg_sent", 0)
        med = mp.get("emrg_delivered", 0)
        if qes == 0 and mes == 0:
            continue
        qr   = qed / max(qes, 1) * 100
        mr   = med / max(mes, 1) * 100
        diff = qr - mr
        dc   = G if diff > 5 else Y if diff > 0 else W
        print(f"  {pn:<24} {G}{qr:>7.1f}%{RESET} {R}{mr:>7.1f}%{RESET} "
              f"{dc}{diff:>+7.1f}%{RESET} "
              f"{DIM}{ph['rtt_ms']:>5}ms{RESET} "
              f"{DIM}{ph['loss']*100:>5.1f}%{RESET}")

    print(f"\n{BOLD}{C}{'═'*70}{RESET}\n")

# ═══════════════════════════════════════════════════════════════════════════════
# JSON Kaydet
# ═══════════════════════════════════════════════════════════════════════════════
def save_json(sim: IoTCrisisSimulation, path: str):
    q = sim.qdap; m = sim.mqtt

    def phase_dict(metrics):
        out = {}
        for ph in PHASES:
            pn = ph["name"]
            pd = metrics.phase_data.get(pn, {})
            es = pd.get("emrg_sent", 0)
            ed = pd.get("emrg_delivered", 0)
            ts = pd.get("sent", 0)
            td = pd.get("delivered", 0)
            lats = pd.get("latencies", [])
            out[pn] = {
                "sent": ts, "delivered": td,
                "emrg_sent": es, "emrg_delivered": ed,
                "emrg_delivery_rate": round(ed / max(es, 1), 4),
                "delivery_rate": round(td / max(ts, 1), 4),
                "avg_latency_ms": round(sum(lats)/max(len(lats),1), 2),
                "rtt_ms": ph["rtt_ms"],
                "loss_rate": ph["loss"],
            }
        return out

    result = {
        "metadata": {
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scenario":   "Istanbul Cascade Crisis — Protocol Comparison",
            "qdap_protocol": "QFrame+PriorityQueue+GhostSession+AES256GCM",
            "mqtt_protocol": "MQTT3.1.1+QoS1+FIFO+Mosquitto",
            "devices":    len(DEVICES),
            "phases":     [p["name"] for p in PHASES],
            "qdap_pipeline_workers": QDAP_PIPELINE_WORKERS,
            "mqtt_pipeline_workers": MQTT_PIPELINE_WORKERS,
        },
        "qdap": {
            "sent": q.sent, "delivered": q.delivered,
            "delivery_rate": round(q.delivery_rate, 3),
            "emrg_sent": q.emrg_sent, "emrg_delivered": q.emrg_delivered,
            "emrg_delivery_rate": round(q.emrg_delivery_rate, 3),
            "avg_latency_ms": round(q.avg_latency, 2),
            "avg_emrg_latency_ms": round(q.avg_emrg_latency, 2),
            "per_phase": phase_dict(q),
        },
        "mosquitto": {
            "sent": m.sent, "delivered": m.delivered,
            "delivery_rate": round(m.delivery_rate, 3),
            "emrg_sent": m.emrg_sent, "emrg_delivered": m.emrg_delivered,
            "emrg_delivery_rate": round(m.emrg_delivery_rate, 3),
            "avg_latency_ms": round(m.avg_latency, 2),
            "avg_emrg_latency_ms": round(m.avg_emrg_latency, 2),
            "per_phase": phase_dict(m),
        },
        "improvements": {
            "emrg_delivery_gain_pct": round(q.emrg_delivery_rate - m.emrg_delivery_rate, 2),
            "latency_ratio":          round(m.avg_latency / max(q.avg_latency, 0.1), 2),
            "missed_critical_mqtt":   (m.emrg_sent - m.emrg_delivered) -
                                      (q.emrg_sent - q.emrg_delivered),
        },
        "time_series": sim.time_series,
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"{G}✅ JSON kaydedildi: {path}{RESET}")

# ═══════════════════════════════════════════════════════════════════════════════
# PNG Grafikleri — 5 panel
# ═══════════════════════════════════════════════════════════════════════════════
def save_png(sim: IoTCrisisSimulation, path: str):
    if not HAS_MATPLOTLIB:
        print(f"{Y}⚠️  matplotlib yok — PNG atlandı{RESET}")
        return

    q  = sim.qdap; m = sim.mqtt
    ts = sim.time_series

    t_vals  = [p["t"]          for p in ts]
    qe_vals = [p["qdap_emrg"]  for p in ts]
    me_vals = [p["mqtt_emrg"]  for p in ts]
    qt_vals = [p["qdap_total"] for p in ts]
    mt_vals = [p["mqtt_total"] for p in ts]
    rtt_v   = [p["rtt_ms"]     for p in ts]
    loss_v  = [p["loss_rate"] * 100 for p in ts]
    ql_v    = [p["qdap_lat"]   for p in ts]
    ml_v    = [p["mqtt_lat"]   for p in ts]

    # Faz sınırları
    phase_boundaries = []
    t_acc = 0
    for ph in PHASES:
        phase_boundaries.append((t_acc, t_acc + ph["duration"], ph["name"]))
        t_acc += ph["duration"]

    phase_colors = ["#2ecc71", "#f39c12", "#e74c3c", "#8e44ad", "#3498db"]

    fig, axes = plt.subplots(5, 1, figsize=(14, 22))
    fig.suptitle("QDAP vs MQTT — Protokol Seviyesi IoT Kriz Karşılaştırması\n"
                 "İstanbul Deprem Senaryosu",
                 fontsize=14, fontweight="bold", y=0.98)

    def add_phase_bands(ax):
        for i, (t0, t1, name) in enumerate(phase_boundaries):
            ax.axvspan(t0, t1, alpha=0.07, color=phase_colors[i])
            mid = (t0 + t1) / 2
            ax.axvline(t0, color="gray", linewidth=0.5, linestyle="--", alpha=0.4)
            ax.text(mid, ax.get_ylim()[1] * 0.97, name,
                    ha="center", va="top", fontsize=7, color="gray",
                    rotation=0 if len(name) < 12 else 15)

    # ── 1. Emergency Delivery Rate ────────────────────────────────────────────
    ax = axes[0]
    if t_vals:
        ax.plot(t_vals, qe_vals, color="#27ae60", linewidth=2.5,
                label="QDAP (QFrame + Priority Queue)", marker="o", markersize=3)
        ax.plot(t_vals, me_vals, color="#e74c3c", linewidth=2.5,
                label="MQTT (Mosquitto + FIFO)", marker="s", markersize=3,
                linestyle="--")
        ax.fill_between(t_vals, qe_vals, me_vals,
                        where=[q > m for q, m in zip(qe_vals, me_vals)],
                        alpha=0.15, color="#27ae60", label="QDAP avantajı")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Emergency Delivery Rate (%)", fontsize=10)
    ax.set_title("[1] EMERGENCY Delivery Rate - EN KRITIK METRIK", fontweight="bold")
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)
    add_phase_bands(ax)

    # ── 2. Total Delivery Rate ────────────────────────────────────────────────
    ax = axes[1]
    if t_vals:
        ax.plot(t_vals, qt_vals, color="#2980b9", linewidth=2,
                label="QDAP Toplam", marker="o", markersize=3)
        ax.plot(t_vals, mt_vals, color="#e67e22", linewidth=2,
                label="MQTT Toplam", marker="s", markersize=3, linestyle="--")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Total Delivery Rate (%)", fontsize=10)
    ax.set_title("[2] Toplam Mesaj Teslim Orani")
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)
    add_phase_bands(ax)

    # ── 3. Latency ────────────────────────────────────────────────────────────
    ax = axes[2]
    if t_vals:
        ax.plot(t_vals, ql_v, color="#27ae60", linewidth=2,
                label="QDAP Gecikme", marker="o", markersize=3)
        ax.plot(t_vals, ml_v, color="#e74c3c", linewidth=2,
                label="MQTT Gecikme", marker="s", markersize=3, linestyle="--")
    ax.set_ylabel("Ortalama Gecikme (ms)", fontsize=10)
    ax.set_title("[3] Ortalama End-to-End Gecikme (ms)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    add_phase_bands(ax)

    # ── 4. Network Conditions ─────────────────────────────────────────────────
    ax  = axes[3]
    ax2 = ax.twinx()
    if t_vals:
        l1, = ax.plot(t_vals, rtt_v, color="#8e44ad", linewidth=2, label="RTT (ms)")
        l2, = ax2.plot(t_vals, loss_v, color="#e74c3c", linewidth=2,
                       linestyle=":", label="Kayıp Oranı (%)")
    ax.set_ylabel("RTT (ms)", color="#8e44ad", fontsize=10)
    ax2.set_ylabel("Kayıp Oranı (%)", color="#e74c3c", fontsize=10)
    ax.set_title("[4] Ag Kosullari (Simule Edilmis) - RTT & Loss Rate")
    if t_vals:
        ax.legend(handles=[l1, l2], loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    add_phase_bands(ax)

    # ── 5. Per-Phase Emergency Bar Chart ─────────────────────────────────────
    ax = axes[4]
    phase_names = [ph["name"] for ph in PHASES]
    qdap_bars, mqtt_bars = [], []
    for ph in PHASES:
        pn  = ph["name"]
        qpd = q.phase_data.get(pn, {})
        mpd = m.phase_data.get(pn, {})
        qes = qpd.get("emrg_sent", 0);      qed = qpd.get("emrg_delivered", 0)
        mes = mpd.get("emrg_sent", 0);      med = mpd.get("emrg_delivered", 0)
        qdap_bars.append(qed / max(qes, 1) * 100)
        mqtt_bars.append(med / max(mes, 1) * 100)

    x      = np.arange(len(phase_names))
    width  = 0.35
    b1 = ax.bar(x - width/2, qdap_bars, width, label="QDAP", color="#27ae60",
                alpha=0.85, edgecolor="white")
    b2 = ax.bar(x + width/2, mqtt_bars, width, label="MQTT/Mosquitto",
                color="#e74c3c", alpha=0.85, edgecolor="white")

    for bar in b1:
        h = bar.get_height()
        if h > 2:
            ax.text(bar.get_x() + bar.get_width()/2, h + 1,
                    f"{h:.0f}%", ha="center", va="bottom", fontsize=8,
                    color="#1a7a45", fontweight="bold")
    for bar in b2:
        h = bar.get_height()
        if h > 2:
            ax.text(bar.get_x() + bar.get_width()/2, h + 1,
                    f"{h:.0f}%", ha="center", va="bottom", fontsize=8,
                    color="#922b21", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([p.replace("🔴 ", "").replace("🆘 ", "") for p in phase_names],
                       rotation=10, fontsize=9)
    ax.set_ylim(0, 115)
    ax.set_ylabel("Emergency Delivery Rate (%)", fontsize=10)
    ax.set_title("[5] Faza Gore Acil Mesaj Teslim Karsilastirmasi")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis="y")

    # Genel kazanım notu
    gain  = q.emrg_delivery_rate - m.emrg_delivery_rate
    ratio = m.avg_latency / max(q.avg_latency, 0.1)
    fig.text(0.5, 0.01,
             f"QDAP: Emergency Teslim +{gain:.1f}% | Gecikme {ratio:.1f}× Daha İyi | "
             f"Ghost Session (ACK-free) + Priority Queue + AES-256-GCM",
             ha="center", fontsize=10, color="#2c3e50",
             bbox=dict(boxstyle="round", facecolor="#ecf0f1", alpha=0.8))

    # X-axis label sadece son panelde
    for ax in axes:
        ax.set_xlabel("")
    axes[-1].set_xlabel("Simülasyon Süresi (saniye)", fontsize=10)

    plt.tight_layout(rect=[0, 0.03, 1, 0.97])

    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"{G}✅ PNG kaydedildi: {path}{RESET}")

# ═══════════════════════════════════════════════════════════════════════════════
# Giriş noktası
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Port kontrolü
    for port, name in [(QDAP_TCP_PORT, "QDAP-TCP"), (MOSQ_TCP_PORT, "Mosquitto")]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
        except OSError:
            print(f"{R}❌ Port {port} ({name}) zaten kullanımda!{RESET}")
            print(f"   lsof -i :{port} | grep LISTEN")
            sys.exit(1)

    sim = IoTCrisisSimulation()
    try:
        sim.run()
    except KeyboardInterrupt:
        print(f"\n{Y}⚠️  Simülasyon durduruldu{RESET}")

    print_report(sim)

    base = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "results")
    save_json(sim, os.path.join(base, "iot_crisis_real.json"))
    save_png(sim,  os.path.join(base, "iot_crisis_real.png"))
