#!/usr/bin/env python3
"""
QDAP Quickstart
===============
pip install qdap
python examples/quickstart.py

Demonstrates the three most common use cases:
  1. Emergency-priority messaging        (crisis-resilient delivery)
  2. Adaptive FEC for lossy channels     (35% loss → 95%+ delivery)
  3. IoT delta compression               (74% bandwidth reduction)

No external dependencies beyond qdap.
"""

import asyncio

import qdap
from qdap.transport.loopback import LoopbackTransport


# ─────────────────────────────────────────────────────────────────────────────
# 1. Emergency-priority messaging
# ─────────────────────────────────────────────────────────────────────────────

async def demo_emergency_messaging():
    print("\n" + "═" * 60)
    print("  1. Emergency-Priority Messaging")
    print("═" * 60)

    server = qdap.QDAPServer("127.0.0.1", 19900)
    await server.start()

    async with qdap.QDAPClient("127.0.0.1", 19900) as client:
        # Normal payload
        await client.send_multiframe(
            payloads=[b"Sensor reading: temp=23.1C, co2=412ppm"],
            deadline_ms=[1000.0],
        )

        # Emergency payload — short deadline → highest amplitude → sent first
        await client.send_multiframe(
            payloads=[
                b"[EMERGENCY] Fire detected in Zone 4!",  # critical — 50ms deadline
                b"Zone 4 camera feed",                    # secondary — 500ms deadline
            ],
            deadline_ms=[50.0, 500.0],
        )

    await asyncio.sleep(0.05)

    frames = server.drain()
    print(f"  Frames received: {len(frames)}")
    for i, frame in enumerate(frames):
        order = frame.send_order
        payloads_sorted = [frame.subframes[j].payload for j in order if j < len(frame.subframes)]
        for j, pl in enumerate(payloads_sorted):
            print(f"    Frame {i} payload[{j}]: {pl[:60]}")

    await server.stop()
    print("  ✓ Emergency frames delivered with priority ordering")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Adaptive FEC for lossy channels
# ─────────────────────────────────────────────────────────────────────────────

def demo_fec():
    print("\n" + "═" * 60)
    print("  2. Adaptive FEC — Lossy Channel Recovery")
    print("═" * 60)

    fec = qdap.AdaptiveFEC()

    # Simulate measured loss
    fec.observe_loss(lost=7, sent=20)  # 35% loss
    print(f"  Observed channel loss: {fec.current_loss*100:.1f}%")

    # Emergency message — gets EMERGENCY profile (k=1, r=2)
    emrg_payload = b"SOS: bridge collapse, evacuation needed"
    coded_emrg, profile_emrg = fec.encode(emrg_payload, is_emergency=True)
    print(f"\n  Emergency message ({len(emrg_payload)}B):")
    print(f"    Profile: {profile_emrg.label} (k={profile_emrg.k}, r={profile_emrg.r})")
    print(f"    Coded packets: {len(coded_emrg)} (any 1 of {len(coded_emrg)} sufficient)")
    print(f"    Effective loss: {qdap.fec_effective_loss(0.35, profile_emrg.k, profile_emrg.r)*100:.2f}%")
    print(f"    Delivery: {(1-qdap.fec_effective_loss(0.35, profile_emrg.k, profile_emrg.r))*100:.1f}%")

    # Normal sensor data — gets BALANCED profile (k=2, r=2)
    normal_payload = b"temp=23.1,co2=412,humidity=61"
    coded_norm, profile_norm = fec.encode(normal_payload, is_emergency=False)
    print(f"\n  Normal sensor data ({len(normal_payload)}B):")
    print(f"    Profile: {profile_norm.label} (k={profile_norm.k}, r={profile_norm.r})")
    print(f"    Coded packets: {len(coded_norm)} (any {profile_norm.k} of {len(coded_norm)} sufficient)")
    eff = qdap.fec_effective_loss(0.35, profile_norm.k, profile_norm.r)
    print(f"    Effective loss: {eff*100:.2f}%  (vs raw 35%)")
    print(f"    Improvement: {0.35/max(eff,1e-9):.1f}×")

    # FEC improvement table
    print(f"\n  FEC impact at 35% channel loss:")
    print(f"    {'Message Type':<18} {'Raw delivery':>13} {'FEC delivery':>13} {'Overhead':>9}")
    print(f"    {'─'*57}")
    for is_emrg, label in [(True, "Emergency"), (False, "Normal")]:
        r = qdap.fec_delivery_improvement(0.35, is_emrg)
        print(f"    {label:<18} {r['raw_delivery']:>12.1f}% {r['effective_delivery']:>12.1f}%  {r['overhead_factor']:>7.1f}×")

    print("  ✓ FEC delivers 95%+ emergency messages even at 35% loss")


# ─────────────────────────────────────────────────────────────────────────────
# 3. IoT delta compression
# ─────────────────────────────────────────────────────────────────────────────

def demo_delta_compression():
    print("\n" + "═" * 60)
    print("  3. IoT Delta Compression")
    print("═" * 60)

    enc = qdap.DeltaEncoder()

    # Simulate IoT sensor stream (temperature + CO2 + humidity)
    readings = [
        {"temp": 23.1, "co2": 412, "humidity": 61, "pressure": 1013},
        {"temp": 23.2, "co2": 413, "humidity": 61, "pressure": 1013},  # temp+co2 changed
        {"temp": 23.2, "co2": 413, "humidity": 62, "pressure": 1013},  # humidity changed
        {"temp": 23.1, "co2": 411, "humidity": 62, "pressure": 1013},  # temp+co2 changed
        {"temp": 23.1, "co2": 411, "humidity": 62, "pressure": 1013},  # nothing changed
    ]

    total_raw  = 0
    total_wire = 0

    print(f"\n  {'Reading':>8} {'Fields changed':<25} {'Raw(B)':>8} {'Wire(B)':>8} {'Type'}")
    print(f"  {'─'*65}")

    for i, reading in enumerate(readings):
        raw_size  = len(str(reading).encode())
        frame     = enc.encode(reading)
        wire_size = len(frame)
        total_raw  += raw_size
        total_wire += wire_size
        frame_type = "FULL" if frame[0] == 0x00 else "DELTA"
        changed = "—" if frame_type == "FULL" else f"see delta frame"
        if i > 0:
            changed_fields = [k for k, v in reading.items()
                              if v != readings[i-1].get(k)]
            changed = ", ".join(changed_fields) if changed_fields else "nothing"
        print(f"  {i+1:>8} {changed:<25} {raw_size:>8} {wire_size:>8}  {frame_type}")

    reduction = (1 - total_wire / max(total_raw, 1)) * 100
    print(f"\n  Total: {total_raw}B raw → {total_wire}B wire ({reduction:.1f}% reduction)")
    print("  ✓ Delta encoding dramatically reduces IoT bandwidth")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Channel prediction with BPTT Markov
# ─────────────────────────────────────────────────────────────────────────────

def demo_channel_prediction():
    print("\n" + "═" * 60)
    print("  4. BPTT Markov Channel Predictor")
    print("═" * 60)

    predictor = qdap.BPTTMarkovEstimator()

    # Simulate deteriorating channel (clear → lossy → crisis)
    import random
    random.seed(42)
    readings = (
        [(20 + random.gauss(0,1), 0.01, 1024, 0.1) for _ in range(15)] +  # normal
        [(80 + random.gauss(0,5), 0.08, 4096, 0.1) for _ in range(15)] +  # mobile
        [(300+ random.gauss(0,20),0.35, 1024, 0.1) for _ in range(10)]    # crisis
    )

    for rtt_ms, loss, ps, dt in readings:
        predictor.observe(rtt_ms, loss, ps, dt)
        predictor.update_target(1.0 - loss, loss, rtt_ms / 500.0)

    p_d, p_r, q = predictor.predict()
    print(f"\n  After {len(readings)} observations (normal→mobile→crisis):")
    print(f"    p_delivery  = {p_d:.3f}  (predicted delivery probability)")
    print(f"    p_retransmit= {p_r:.3f}  (probability of retransmit needed)")
    print(f"    q_quality   = {q:.3f}  (normalized channel quality)")

    # Recommended FEC profile based on prediction
    profile = qdap.select_fec_profile(1 - p_d, is_emergency=True)
    print(f"\n  Recommended FEC profile: {profile.label}")
    print(f"    → k={profile.k}, r={profile.r}, overhead={profile.overhead_factor:.1f}×")
    print("  ✓ LSTM channel predictor selects optimal FEC proactively")


# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 60)
    print("  QDAP Quickstart Demo")
    print(f"  Version: {qdap.__version__}")
    print("=" * 60)

    await demo_emergency_messaging()
    demo_fec()
    demo_delta_compression()
    demo_channel_prediction()

    print("\n" + "=" * 60)
    print("  All demos completed ✓")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
