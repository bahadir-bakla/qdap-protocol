#!/bin/bash
set -e

echo "=== QDAP Rust Hot-Path Build ==="

# Rust yüklü mü?
if ! command -v cargo &> /dev/null; then
    echo "❌ Rust yüklü değil. Yükle: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# maturin yüklü mü?
if ! command -v maturin &> /dev/null; then
    echo "📦 maturin yükleniyor..."
    pip install maturin --break-system-packages
fi

cd qdap_core

echo "🦀 Rust release build başlıyor..."
maturin develop --release

echo "✅ Build tamamlandı!"
echo ""

# Test et
python3 -c "
import qdap_core
print('Backend:', qdap_core.__backend__)
print('Version:', qdap_core.__version__)

# Hızlı doğrulama
payload = b'test' * 1000
digest  = qdap_core.hash_frame(payload)
print(f'SHA3-256: {digest.hex()[:16]}... ✅')

key   = b'\x42' * 32
nonce = b'\x01' * 12
ct    = qdap_core.encrypt_frame(key, nonce, b'hello', b'')
print(f'AES-GCM encrypt: {len(ct)} bytes ✅')

priv, pub = qdap_core.x25519_generate_keypair()
print(f'X25519 keypair: priv={len(priv)}B pub={len(pub)}B ✅')

print('')
print('🚀 Rust backend aktif — hardware acceleration hazır!')
"
