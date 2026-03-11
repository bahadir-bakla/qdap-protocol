# benchmarks/qframe_scheduler_benchmark.py

import time
from qdap._rust_bridge import (
    qframe_serialize, qframe_deserialize,
    qft_decide, backend_info
)

def bench(name, fn, n=10000):
    t0 = time.monotonic()
    for _ in range(n):
        fn()
    elapsed = time.monotonic() - t0
    ms = elapsed / n * 1000
    print(f"  {name:<40} {ms:.4f} ms/op")
    return ms

print(f"\n=== QFrame + Scheduler Benchmark ===")
print(f"Backend: {backend_info()['backend'].upper()}\n")

payload_1kb  = b"X" * 1024
payload_64kb = b"X" * 65536

print("[QFrame Serialize]")
bench("qframe_serialize(1KB)",   lambda: qframe_serialize(payload_1kb,  0, 500.0, 1, 0))
bench("qframe_serialize(64KB)",  lambda: qframe_serialize(payload_64kb, 0, 500.0, 1, 0))

wire_1kb  = qframe_serialize(payload_1kb,  0, 500.0, 1, 0)
wire_64kb = qframe_serialize(payload_64kb, 0, 500.0, 1, 0)

print("\n[QFrame Deserialize]")
bench("qframe_deserialize(1KB)",  lambda: qframe_deserialize(wire_1kb))
bench("qframe_deserialize(64KB)", lambda: qframe_deserialize(wire_64kb))

print("\n[QFT Scheduler]")
bench("qft_decide(1KB,  20ms, 1% loss)",  lambda: qft_decide(1024,        20.0, 0.01), n=100000)
bench("qft_decide(1MB,  50ms, 5% loss)",  lambda: qft_decide(1048576,     50.0, 0.05), n=100000)
bench("qft_decide(10MB, 2ms,  0% loss)",  lambda: qft_decide(10485760,    2.0,  0.0),  n=100000)

print("""
Beklenen (Rust backend):
  qframe_serialize(1KB):    < 0.01 ms/op
  qframe_deserialize(1KB):  < 0.01 ms/op
  qft_decide():             < 0.001 ms/op (>1M decisions/sec)
""")
