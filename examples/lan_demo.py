#!/usr/bin/env python3
"""
QDAP LAN Demo — Two Physical Devices
======================================
pip install qdap   (or: pip install -e . from repo)

DEVICE A (server):
    python examples/lan_demo.py server

DEVICE B (client):
    python examples/lan_demo.py client 192.168.1.50

Replace 192.168.1.50 with Device A's LAN IP (ipconfig / ip addr).
Both devices must be on the same network (WiFi, Ethernet, hotspot).
"""

import asyncio
import sys
import time

import qdap
from qdap import QDAPServer, QDAPClient, AdaptiveFEC, DeltaEncoder

PORT = 19876


# ─────────────────────────────────────────────────────────────────────────────
# SERVER — run on Device A
# ─────────────────────────────────────────────────────────────────────────────

async def run_server():
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())

    print(f"\n{'='*55}")
    print(f"  QDAP LAN Server  —  v{qdap.__version__}")
    print(f"{'='*55}")
    print(f"  Listening on  0.0.0.0:{PORT}")
    print(f"  Device A IP   {local_ip}")
    print(f"  Tell Device B: python lan_demo.py client {local_ip}")
    print(f"{'='*55}\n")

    server = QDAPServer("0.0.0.0", PORT)
    await server.start()
    print("  [server] ready — waiting for frames...\n")

    received = 0
    try:
        while True:
            await asyncio.sleep(0.1)
            payloads = server.drain_payloads()
            for payload in payloads:
                received += 1
                text = payload.decode(errors="replace")
                tag  = "🚨 EMERGENCY" if text.startswith("[EMERGENCY]") else "📦 data"
                print(f"  [{received:>3}] {tag}  {text[:72]}")
    except KeyboardInterrupt:
        print(f"\n  [server] stopping — {received} frames received total")
        await server.stop()


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT — run on Device B
# ─────────────────────────────────────────────────────────────────────────────

async def run_client(server_ip: str):
    print(f"\n{'='*55}")
    print(f"  QDAP LAN Client  —  v{qdap.__version__}")
    print(f"{'='*55}")
    print(f"  Connecting to  {server_ip}:{PORT}")
    print(f"{'='*55}\n")

    fec = AdaptiveFEC()
    fec.observe_loss(lost=0, sent=20)   # LAN — clean channel

    delta = DeltaEncoder()

    # Wait for server to be reachable (retry up to 10s)
    import socket as _sock
    for attempt in range(20):
        try:
            s = _sock.create_connection((server_ip, PORT), timeout=0.5)
            s.close()
            break
        except OSError:
            if attempt == 19:
                print(f"  ERROR: cannot reach {server_ip}:{PORT} — is the server running?")
                return
            await asyncio.sleep(0.5)

    async with QDAPClient(server_ip, PORT) as client:

        # ── 1. Normal sensor readings ──────────────────────────────────────
        print("  [1] Sending IoT sensor stream (5 readings)...")
        readings = [
            {"temp": 23.1, "co2": 412, "humidity": 61},
            {"temp": 23.2, "co2": 413, "humidity": 61},
            {"temp": 23.4, "co2": 415, "humidity": 62},
            {"temp": 23.3, "co2": 414, "humidity": 62},
            {"temp": 23.3, "co2": 414, "humidity": 62},
        ]
        for r in readings:
            await client.send_multiframe(
                payloads=[str(r).encode()],
                deadline_ms=[500.0],
            )
            await asyncio.sleep(0.1)
        print("      ✓ sensor stream sent\n")

        # ── 2. Emergency message ───────────────────────────────────────────
        print("  [2] Sending EMERGENCY frame (50ms deadline)...")
        await client.send_multiframe(
            payloads=[b"[EMERGENCY] Fire detected in Zone 4 - evacuate now!"],
            deadline_ms=[50.0],
        )
        print("      ✓ emergency frame sent\n")

        # ── 3. Bulk payload (delta compressed) ────────────────────────────
        print("  [3] Sending bulk telemetry (20 frames, delta compressed)...")
        base = {"node": "gw-01", "uptime": 0, "packets": 0, "rssi": -65}
        for i in range(20):
            base["uptime"]  = i * 10
            base["packets"] = i * 42
            compressed = delta.encode(base)
            await client.send_multiframe(
                payloads=[compressed],
                deadline_ms=[1000.0],
            )
            await asyncio.sleep(0.05)
        print("      ✓ bulk telemetry sent\n")

    # Summary
    t = time.strftime("%H:%M:%S")
    print(f"  [{t}] All done — 26 frames sent to {server_ip}:{PORT}")
    print(f"  Protocol:  QDAP v{qdap.__version__}")
    print(f"  Transport: TCP (asyncio)")
    print(f"  FEC:       {fec.current_loss*100:.0f}% observed loss → profile AUTO\n")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("server", "client"):
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "server":
        asyncio.run(run_server())
    else:
        if len(sys.argv) < 3:
            print("Usage: python lan_demo.py client <SERVER_IP>")
            sys.exit(1)
        asyncio.run(run_client(sys.argv[2]))


if __name__ == "__main__":
    main()
