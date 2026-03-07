# wan_benchmark/wan_receiver.py
"""
WAN test receiver.
Windows veya Linux'ta çalışır.
3 server başlatır:
  19600: Classical TCP receiver (ACK gönderir)
  19601: QDAP Ghost Session receiver (ACK göndermez)
  19602: QDAP Secure receiver (X25519 + AES-GCM)
"""

import asyncio
import struct
import time
import json
import pathlib
from collections import defaultdict

# Sayaçlar
stats = defaultdict(lambda: {
    "received": 0,
    "bytes": 0,
    "t_first": None,
    "t_last": None,
})

ACK = struct.pack(">Q", 0xDEADBEEFCAFEBABE)   # 8 byte ACK


# ── Classical Receiver ───────────────────────────────────────────

async def classical_handler(reader, writer):
    """Her mesaja ACK döner."""
    peer = writer.get_extra_info("peername")
    while True:
        try:
            # Length prefix oku
            length_bytes = await asyncio.wait_for(
                reader.readexactly(4), timeout=60.0
            )
            length = struct.unpack(">I", length_bytes)[0]
            if length == 0:
                break

            payload = await asyncio.wait_for(
                reader.readexactly(length), timeout=60.0
            )

            s = stats["classical"]
            s["received"] += 1
            s["bytes"]    += length
            if s["t_first"] is None:
                s["t_first"] = time.monotonic()
            s["t_last"] = time.monotonic()

            # ACK gönder
            writer.write(ACK)
            await writer.drain()

        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break

    writer.close()


# ── Ghost Session Receiver ───────────────────────────────────────

async def ghost_handler(reader, writer):
    """ACK GÖNDERMEZ — Ghost Session."""
    while True:
        try:
            length_bytes = await asyncio.wait_for(
                reader.readexactly(4), timeout=60.0
            )
            length = struct.unpack(">I", length_bytes)[0]
            if length == 0:
                break

            payload = await asyncio.wait_for(
                reader.readexactly(length), timeout=60.0
            )

            s = stats["ghost"]
            s["received"] += 1
            s["bytes"]    += length
            if s["t_first"] is None:
                s["t_first"] = time.monotonic()
            s["t_last"] = time.monotonic()

            # ACK YOK — Ghost Session

        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break

    writer.close()


# ── Secure Ghost Receiver ────────────────────────────────────────

async def secure_handler(reader, writer):
    """X25519 handshake + AES-GCM decrypt. ACK göndermez."""
    try:
        from qdap.security.handshake import perform_server_handshake
        from qdap.security.encrypted_frame import FrameEncryptor

        # Handshake
        session_keys = await perform_server_handshake(reader, writer)
        decryptor    = FrameEncryptor(session_keys.data_key)

        while True:
            try:
                length_bytes = await asyncio.wait_for(
                    reader.readexactly(4), timeout=60.0
                )
                length = struct.unpack(">I", length_bytes)[0]
                if length == 0:
                    break

                wire = await asyncio.wait_for(
                    reader.readexactly(length), timeout=60.0
                )

                result = decryptor.unpack(wire)
                if not result.verified:
                    print("⚠️  Frame authentication failed!")
                    break

                s = stats["secure"]
                s["received"] += 1
                s["bytes"]    += len(result.plaintext)
                if s["t_first"] is None:
                    s["t_first"] = time.monotonic()
                s["t_last"] = time.monotonic()

            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
                break

    except Exception as e:
        print(f"Secure handler error: {e}")

    writer.close()


# ── Stats HTTP Server ─────────────────────────────────────────────

async def stats_handler(reader, writer):
    """GET /stats → mevcut sayaçları döndür."""
    await reader.read(1024)
    body = json.dumps({
        k: {
            "received":   v["received"],
            "bytes":      v["bytes"],
            "duration_s": (v["t_last"] - v["t_first"])
                          if v["t_first"] and v["t_last"] else 0,
        }
        for k, v in stats.items()
    })
    resp = (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n{body}"
    )
    writer.write(resp.encode())
    await writer.drain()
    writer.close()


async def reset_handler(reader, writer):
    """POST /reset → sayaçları sıfırla."""
    await reader.read(1024)
    stats.clear()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()


async def control_handler(reader, writer):
    req = (await reader.read(64)).decode(errors="ignore")
    if "reset" in req.lower():
        await reset_handler(reader, writer)
    else:
        await stats_handler(reader, writer)


async def main():
    servers = await asyncio.gather(
        asyncio.start_server(classical_handler, "0.0.0.0", 19600),
        asyncio.start_server(ghost_handler,     "0.0.0.0", 19601),
        asyncio.start_server(secure_handler,    "0.0.0.0", 19602),
        asyncio.start_server(control_handler,   "0.0.0.0", 19603),
    )
    print("✅ WAN Receiver hazır:")
    print("  19600/TCP — Classical (ACK)")
    print("  19601/TCP — QDAP Ghost Session")
    print("  19602/TCP — QDAP Secure (X25519+AES)")
    print("  19603/TCP — Stats/Control")
    print("\nBu pencereyi açık bırak — Mac'ten bağlantı bekleniyor...")

    async with asyncio.TaskGroup() as tg:
        for s in servers:
            tg.create_task(s.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
