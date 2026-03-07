"""
QDAP Server: Receives QFrames, sends NO ACK.
==============================================

Ghost Session handles loss detection implicitly.
This is the key difference from classical server.
"""

import asyncio
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from qdap.frame.qframe import QFrame


async def handle_qdap(reader, writer):
    peer = writer.get_extra_info('peername')
    print(f"[QDAP Server] Connection from {peer}")
    frames_received = 0

    from qdap.security.encrypted_frame import FrameEncryptor
    encryptor = FrameEncryptor(b"A" * 32)  # Static mock key

    try:
        while True:
            # Read QDAP wire header: [MAGIC(4)|VERSION(2)|LENGTH(4)] = 10 bytes
            header = await reader.readexactly(10)
            magic = header[:4]
            length = struct.unpack(">I", header[6:10])[0]

            # Read frame body
            frame_bytes = await reader.readexactly(length)

            # Deserialize (validates frame)
            frame = QFrame.deserialize(frame_bytes)
            
            # ADIM 2: SECURE GHOST SESSION OVERHEAD
            for chunk in frame.subframes:
                if len(chunk.payload) >= 28:
                    _ = encryptor.unpack(chunk.payload)
                
            frames_received += 1

            # *** NO ACK SENT *** — this is QDAP's key feature
            # Ghost Session on the sender side handles loss detection

    except (asyncio.IncompleteReadError, ConnectionResetError, ConnectionError):
        pass
    finally:
        print(f"[QDAP Server] Client disconnected. Frames received: {frames_received}")
        writer.close()


async def start_qdap_server(host="0.0.0.0", port=19601):
    server = await asyncio.start_server(handle_qdap, host, port)
    print(f"[QDAP Server] Listening on {host}:{port}")
    async with server:
        await server.serve_forever()
