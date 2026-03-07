"""
Start All Servers (TCP + UDP)
=================================

TCP:  Classical (19600) + QDAP (19601)
UDP:  ReqResp (19700) + QDAP Sink (19701)
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from docker_benchmark.receiver.classical_server import start_classical_server
from docker_benchmark.receiver.qdap_server import start_qdap_server


async def main():
    print("[Receiver] Starting all servers (TCP)...")
    await asyncio.gather(
        start_classical_server("0.0.0.0", 19600),
        start_qdap_server("0.0.0.0", 19601),
    )


if __name__ == "__main__":
    asyncio.run(main())
