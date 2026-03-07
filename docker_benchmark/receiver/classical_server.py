"""
Classical Server: Every message gets an 8-byte ACK.
=====================================================
"""

import asyncio
import struct


ACK_SIZE = 8


async def handle_classical(reader, writer):
    peer = writer.get_extra_info('peername')
    print(f"[Classical] Connection from {peer}")

    try:
        while True:
            # Read length header (4 bytes)
            length_bytes = await reader.readexactly(4)
            msg_len = struct.unpack(">I", length_bytes)[0]

            # Read message body: [msg_id(4B)] + [payload]
            body = await reader.readexactly(msg_len)
            msg_id = struct.unpack(">I", body[:4])[0]

            # Send 8-byte ACK: [msg_id(4B)][status=0x01(1B)][padding(3B)]
            ack = struct.pack(">IB3s", msg_id, 0x01, b"\x00\x00\x00")
            writer.write(ack)
            await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionError):
        pass
    finally:
        writer.close()


async def start_classical_server(host="0.0.0.0", port=19600):
    server = await asyncio.start_server(handle_classical, host, port)
    print(f"[Classical Server] Listening on {host}:{port}")
    async with server:
        await server.serve_forever()
