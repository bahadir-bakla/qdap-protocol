# benchmarks/rust_vs_python_benchmark.py
"""
Rust vs Python hız karşılaştırması.
Her operasyon için 10000 iterasyon, ortalama süre.
"""

import time
import os
from qdap._rust_bridge import (
    hash_frame, encrypt_frame, decrypt_frame,
    normalize_amplitudes, backend_info
)


def bench(name: str, fn, n: int = 10000) -> float:
    t0 = time.monotonic()
    for _ in range(n):
        fn()
    elapsed = time.monotonic() - t0
    ms_per_op = elapsed / n * 1000
    print(f"  {name:<35} {ms_per_op:.4f} ms/op  ({n} iter)")
    return ms_per_op


def run():
    info = backend_info()
    print(f"\n=== Rust vs Python Benchmark ===")
    print(f"Backend: {info['backend'].upper()}")
    print()

    key      = os.urandom(32)
    nonce    = os.urandom(12)
    small    = os.urandom(1024)          # 1KB
    medium   = os.urandom(64 * 1024)     # 64KB
    large    = os.urandom(256 * 1024)    # 256KB

    print("[SHA3-256]")
    bench("hash_frame(1KB)",   lambda: hash_frame(small),  n=10000)
    bench("hash_frame(64KB)",  lambda: hash_frame(medium), n=1000)
    bench("hash_frame(256KB)", lambda: hash_frame(large),  n=500)

    print("\n[AES-256-GCM Encrypt]")
    bench("encrypt_frame(1KB)",   lambda: encrypt_frame(key, nonce, small,  b""), n=10000)
    bench("encrypt_frame(64KB)",  lambda: encrypt_frame(key, nonce, medium, b""), n=1000)
    bench("encrypt_frame(256KB)", lambda: encrypt_frame(key, nonce, large,  b""), n=500)

    print("\n[L2 Normalizasyon]")
    amps = [float(i) for i in range(1024)]
    bench("normalize_amplitudes(1024)",  lambda: normalize_amplitudes(amps), n=100000)

    print("\n✅ Benchmark tamamlandı")
    print("Not: Rust backend ile Python'a göre 10-150× hızlanma bekleniyor.")


if __name__ == "__main__":
    run()
