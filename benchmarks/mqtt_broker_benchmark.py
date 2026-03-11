# benchmarks/mqtt_broker_benchmark.py
"""
QDAP Broker vs Mosquitto throughput karşılaştırması.
Mosquitto kurulu değilse sadece QDAP ölçer.
"""
import asyncio, time, socket, struct, threading
import json, os

def _build_connect_raw(client_id: str) -> bytes:
    import sys; sys.path.insert(0, "src")
    from qdap.broker.packet_parser import encode_string, encode_remaining_length
    proto = encode_string("MQTT") + bytes([5])
    body = proto + bytes([0x02]) + struct.pack("!H", 60) + bytes([0]) + encode_string(client_id)
    return bytes([0x10]) + encode_remaining_length(len(body)) + body

def benchmark_qdap_broker(n_messages: int = 10000, payload_size: int = 64) -> dict:
    from src.qdap.broker.broker import QDAPBroker
    from src.qdap.broker.packet_parser import build_publish, encode_remaining_length, encode_string

    broker = QDAPBroker(host="127.0.0.1", port=0)
    loop = asyncio.new_event_loop()
    port_holder = [None]

    async def _run():
        server = await asyncio.start_server(broker.handle_client, "127.0.0.1", 0)
        port_holder[0] = server.sockets[0].getsockname()[1]
        async with server:
            await server.serve_forever()

    def _thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    time.sleep(0.5)

    port = port_holder[0]
    payload = b"X" * payload_size
    topic = "benchmark/throughput"

    # Sub client
    sub = socket.socket(); sub.connect(("127.0.0.1", port))
    sub.sendall(_build_connect_raw("bench_sub"))
    sub.recv(1024)
    body = struct.pack("!H", 1) + bytes([0]) + encode_string(topic) + bytes([0])
    sub.sendall(bytes([0x82]) + encode_remaining_length(len(body)) + body)
    sub.recv(1024)

    # Pub client
    pub = socket.socket(); pub.connect(("127.0.0.1", port))
    pub.sendall(_build_connect_raw("bench_pub"))
    pub.recv(1024)

    start = time.time()
    for _ in range(n_messages):
        pub.sendall(build_publish(topic, payload))
    pub_time = time.time() - start

    throughput = (n_messages * payload_size * 8) / pub_time / 1e6  # Mbps
    msg_rate = n_messages / pub_time

    sub.close(); pub.close()
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2.0)

    return {
        "broker": "QDAP",
        "messages": n_messages,
        "payload_bytes": payload_size,
        "duration_s": round(pub_time, 3),
        "throughput_mbps": round(throughput, 2),
        "msg_per_sec": round(msg_rate, 0)
    }

if __name__ == "__main__":
    print("Benchmarking QDAP Broker...")
    results = []
    for size in [64, 1024, 65536]:
        r = benchmark_qdap_broker(n_messages=5000, payload_size=size)
        results.append(r)
        print(f"  {size}B: {r['throughput_mbps']} Mbps, {r['msg_per_sec']} msg/s")

    os.makedirs("benchmarks/results", exist_ok=True)
    with open("benchmarks/results/mqtt_broker_benchmark.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved → benchmarks/results/mqtt_broker_benchmark.json")
