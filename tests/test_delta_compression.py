import pytest
from qdap.compression.delta_encoder import DeltaEncoder, DeltaDecoder


def roundtrip(messages):
    """Encode + decode roundtrip, sonuçları döndür."""
    enc = DeltaEncoder()
    dec = DeltaDecoder()
    decoded = []
    for msg in messages:
        frame = enc.encode(msg)
        result = dec.decode(frame)
        decoded.append(result)
    return decoded, enc


def test_full_roundtrip_single():
    msgs = [{"temp": 23.1, "co2": 412}]
    decoded, _ = roundtrip(msgs)
    assert decoded[0] == msgs[0]


def test_delta_roundtrip_sequence():
    msgs = [
        {"temp": 23.1, "humidity": 65, "co2": 412},
        {"temp": 23.2, "humidity": 65, "co2": 412},  # sadece temp değişti
        {"temp": 23.2, "humidity": 66, "co2": 413},  # iki field değişti
    ]
    decoded, enc = roundtrip(msgs)
    for i, (orig, dec) in enumerate(zip(msgs, decoded)):
        assert orig == dec, f"Mismatch at index {i}: {orig} != {dec}"


def test_delta_smaller_than_full():
    """Delta frame full frame'den küçük olmalı."""
    enc = DeltaEncoder()

    full_data = {"temp": 23.1, "humidity": 65, "pressure": 1013.2,
                 "co2": 412, "battery": 3.7, "rssi": -72}
    delta_data = dict(full_data)
    delta_data["temp"] = 23.2  # sadece bir field değişti

    full_frame  = enc.encode(full_data)
    delta_frame = enc.encode(delta_data)

    assert len(delta_frame) < len(full_frame), (
        f"Delta ({len(delta_frame)}B) should be smaller than "
        f"full ({len(full_frame)}B)"
    )


def test_no_change_minimal_frame():
    """Hiçbir şey değişmediyse frame çok küçük olmalı."""
    enc = DeltaEncoder()
    data = {"temp": 23.1, "co2": 412}
    enc.encode(data)            # baseline kur
    frame = enc.encode(data)    # aynı veri

    # FRAME_DELTA(1) + bitmask(2) = 3 byte
    assert len(frame) == 3


def test_compression_ratio_tracking():
    enc = DeltaEncoder()
    data = {"temp": 23.0, "co2": 400}
    enc.encode(data)  # full
    for i in range(9):
        data2 = dict(data)
        data2["temp"] = 23.0 + i * 0.1
        enc.encode(data2)  # delta
    assert enc.compression_ratio >= 0.8  # 9/10 delta


def test_field_structure_change_resets():
    """Field yapısı değişince full gönder."""
    enc = DeltaEncoder()
    dec = DeltaDecoder()

    frame1 = enc.encode({"temp": 23.1})
    frame2 = enc.encode({"temp": 23.1, "co2": 412})  # yeni field eklendi

    dec.decode(frame1)
    result = dec.decode(frame2)
    assert result == {"temp": 23.1, "co2": 412}


def test_decoder_without_baseline_returns_none():
    """Baseline olmadan delta frame gelirse None döner."""
    dec = DeltaDecoder()
    # Delta frame (bitmask=1, tek field)
    import struct
    frame = bytes([0x01]) + struct.pack(">H", 1) + b'\x00'
    result = dec.decode(frame)
    assert result is None


def test_real_iot_compression_ratio():
    """
    Gerçekçi sensör verisi ile %60+ kompresyon beklenir.
    """
    import random
    enc = DeltaEncoder()
    dec = DeltaDecoder()

    base = {"temp": 23.0, "humidity": 65, "pressure": 1013.2,
            "co2": 412, "battery": 3.7}

    total_full = 0
    total_delta = 0

    for i in range(100):
        # Her adımda küçük değişim (gerçek sensör davranışı)
        data = dict(base)
        data["temp"] += random.gauss(0, 0.1)
        data["co2"]  += random.randint(-2, 2)
        if random.random() < 0.1:
            data["humidity"] += random.randint(-1, 1)

        frame = enc.encode(data)
        decoded = dec.decode(frame)
        assert decoded is not None

        # Boyut karşılaştırması
        import json
        full_size  = len(json.dumps(data).encode())
        total_full  += full_size
        total_delta += len(frame)

    compression = 1 - total_delta / total_full
    print(f"\n  Real IoT compression: {compression:.1%}")
    print(f"  Total full: {total_full}B → delta: {total_delta}B")
    assert compression > 0.50, f"Expected >50% compression, got {compression:.1%}"
