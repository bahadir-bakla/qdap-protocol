"""
Delta compression for repetitive IoT sensor data.

Protokol:
  - FULL frame: ilk mesaj veya delta > threshold (varsayılan %30)
  - DELTA frame: sadece değişen field'lar gönderilir

Format (binary, küçük overhead):
  FULL:  [0x00][msgpack_payload]
  DELTA: [0x01][bitmask_2B][changed_fields_msgpack]

msgpack tercih edildi — JSON'dan 2-4× daha kompakt,
binary safe, standart kütüphane var.
"""

import struct
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    import json
    HAS_MSGPACK = False


FRAME_FULL  = 0x00
FRAME_DELTA = 0x01
DELTA_THRESHOLD = 0.50   # değişim oranı > %50 → full gönder
MAX_FIELDS = 16           # bitmask için max field sayısı


def _encode(data: Any) -> bytes:
    if HAS_MSGPACK:
        return msgpack.packb(data, use_bin_type=True)
    return json.dumps(data).encode()


def _decode(data: bytes) -> Any:
    if HAS_MSGPACK:
        return msgpack.unpackb(data, raw=False)
    return json.loads(data.decode())


@dataclass
class DeltaEncoder:
    """
    Gönderen tarafta delta encoder.

    Her device için ayrı bir DeltaEncoder instance'ı.

    Usage:
        enc = DeltaEncoder()
        frame = enc.encode({"temp": 23.1, "co2": 412})
        # İlk çağrı → FULL frame
        frame = enc.encode({"temp": 23.2, "co2": 412})
        # Sonraki → DELTA frame (sadece temp değişti)
    """
    _baseline:     Optional[Dict]  = field(default=None, repr=False)
    _field_order:  Optional[list]  = field(default=None, repr=False)
    _total_sent:   int = 0
    _delta_count:  int = 0
    _bytes_saved:  int = 0

    def encode(self, data: Dict) -> bytes:
        """
        Veriyi encode et.
        Returns: wire-format bytes (FULL veya DELTA)
        """
        self._total_sent += 1

        # İlk mesaj veya field yapısı değişti → FULL
        if self._baseline is None or set(data.keys()) != set(self._baseline.keys()):
            return self._encode_full(data)

        # Delta hesapla
        changed = {
            k: v for k, v in data.items()
            if v != self._baseline.get(k)
        }
        change_ratio = len(changed) / max(len(data), 1)

        # Çok fazla değişim → FULL daha verimli
        if change_ratio > DELTA_THRESHOLD or len(data) > MAX_FIELDS:
            return self._encode_full(data)

        return self._encode_delta(changed, data)

    def _encode_full(self, data: Dict) -> bytes:
        self._baseline   = dict(data)
        self._field_order = list(data.keys())
        payload = _encode(data)
        return bytes([FRAME_FULL]) + payload

    def _encode_delta(self, changed: Dict, full_data: Dict) -> bytes:
        if not changed:
            # Hiçbir şey değişmedi — sadece bitmask=0
            self._baseline = dict(full_data)
            return bytes([FRAME_DELTA]) + b'\x00\x00'

        # Bitmask: hangi field'lar değişti?
        bitmask = 0
        values  = {}
        for i, key in enumerate(self._field_order):
            if key in changed:
                bitmask |= (1 << i)
                values[key] = changed[key]

        self._baseline.update(changed)

        payload      = _encode(values)
        full_payload = _encode(full_data)  # boyut karşılaştırması için
        self._bytes_saved += len(full_payload) - len(payload) - 2

        self._delta_count += 1
        return bytes([FRAME_DELTA]) + struct.pack(">H", bitmask) + payload

    @property
    def compression_ratio(self) -> float:
        """Delta frame oranı (0-1)."""
        return self._delta_count / max(self._total_sent, 1)

    @property
    def bytes_saved(self) -> int:
        return max(self._bytes_saved, 0)

    def reset(self):
        """Yeni session başlangıcında baseline'ı sıfırla."""
        self._baseline   = None
        self._field_order = None


@dataclass
class DeltaDecoder:
    """
    Alıcı tarafında delta decoder.

    Usage:
        dec = DeltaDecoder()
        data = dec.decode(frame_bytes)
    """
    _baseline:    Optional[Dict] = field(default=None, repr=False)
    _field_order: Optional[list] = field(default=None, repr=False)

    def decode(self, frame: bytes) -> Optional[Dict]:
        """
        Wire-format bytes'ı decode et.
        Returns: tam Dict veya None (hatalı frame)
        """
        if not frame:
            return None

        frame_type = frame[0]
        body       = frame[1:]

        if frame_type == FRAME_FULL:
            data = _decode(body)
            self._baseline   = dict(data)
            self._field_order = list(data.keys())
            return data

        elif frame_type == FRAME_DELTA:
            if self._baseline is None:
                return None  # Baseline yok, full bekle

            if len(body) < 2:
                return None

            bitmask = struct.unpack_from(">H", body, 0)[0]
            if bitmask == 0:
                return dict(self._baseline)  # Hiçbir şey değişmedi

            changed = _decode(body[2:])
            result  = dict(self._baseline)
            result.update(changed)
            self._baseline = result
            return result

        return None

    def reset(self):
        self._baseline   = None
        self._field_order = None
