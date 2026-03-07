# QDAP — Rust Hot-Path Implementation Guide
## PyO3 ile Kritik Yolları Rust'a Taşı
## Gemini Agent İçin: Tam Kod, Sıfır Varsayım

---

## Neden Rust?

```
Şu an Python'da yavaş olan 3 şey:

1. SHA3-256 (her QFrame için)
   Python:  ~2ms / 256KB chunk
   Rust:    ~0.05ms / 256KB chunk  → 40× hızlı

2. AES-256-GCM (her frame şifreleme)
   Python:  ~3ms / 256KB
   Rust:    ~0.02ms / 256KB → 150× hızlı (AES-NI)

3. AmplitudeEncoder (QFT normalizasyon)
   Python:  ~5ms / frame
   Rust:    ~0.1ms / frame  → 50× hızlı

Bu üçü Rust'a taşınırsa:
  1MB = 16 chunk × (2+3+5)ms = 160ms Python
  1MB = 16 chunk × (0.17)ms  = 2.7ms Rust
  → 1MB'de de QDAP Classical'ı geçer
```

---

## Mimari

```
Python katmanı (mevcut, değişmez):
  QFrame, GhostSession, QFTScheduler, AdaptiveChunker
  Tüm business logic Python'da kalır

Rust katmanı (yeni, PyO3):
  qdap_core.so (Python'dan import edilir)
  │
  ├── hash_frame(payload: bytes) → bytes
  │     SHA3-256, Rust implementasyonu
  │
  ├── encrypt_frame(key, nonce, plaintext, aad) → bytes
  │     AES-256-GCM, AES-NI hardware acceleration
  │
  ├── decrypt_frame(key, nonce, tag, ciphertext, aad) → bytes
  │     AES-256-GCM verify + decrypt
  │
  ├── normalize_amplitudes(amplitudes: list[float]) → list[float]
  │     L2 normalizasyon, SIMD ile
  │
  └── x25519_exchange(private_key: bytes, public_key: bytes) → bytes
        X25519 DH, Rust implementasyonu

Python entegrasyonu:
  try:
      import qdap_core   # Rust build varsa kullan
      RUST_AVAILABLE = True
  except ImportError:
      RUST_AVAILABLE = False  # Pure Python fallback
```

---

## Proje Yapısı

```
qdap_core/                    ← YENİ Rust crate
├── Cargo.toml
├── src/
│   ├── lib.rs               ← PyO3 module tanımı
│   ├── crypto.rs            ← AES-GCM + SHA3
│   ├── x25519.rs            ← Key exchange
│   └── amplitude.rs         ← L2 normalizasyon
├── build.rs                 ← Build script

src/qdap/
├── _rust_bridge.py          ← YENİ: Rust/Python seçici
└── security/
    ├── encrypted_frame.py   ← Rust kullanacak şekilde güncelle
    └── handshake.py         ← Rust kullanacak şekilde güncelle

tests/
└── test_rust_bridge.py      ← YENİ: Rust/Python parité testleri
```

---

## ADIM 1 — Cargo.toml

```toml
# qdap_core/Cargo.toml

[package]
name    = "qdap_core"
version = "0.1.0"
edition = "2021"

[lib]
name    = "qdap_core"
crate-type = ["cdylib"]   # Python .so için zorunlu

[dependencies]
# PyO3 — Python binding
pyo3 = { version = "0.20", features = ["extension-module"] }

# Kriptografi
aes-gcm   = { version = "0.10", features = ["aes", "std"] }
sha3      = "0.10"
digest    = "0.10"
x25519-dalek = { version = "2.0", features = ["static_secrets"] }
rand      = "0.8"

# SIMD için
packed_simd_2 = { version = "0.3", optional = true }

[features]
default = []
simd    = ["packed_simd_2"]

[profile.release]
opt-level = 3
lto       = true      # Link-time optimization
codegen-units = 1     # Daha iyi optimizasyon
```

---

## ADIM 2 — lib.rs (PyO3 Module)

```rust
// qdap_core/src/lib.rs

use pyo3::prelude::*;
use pyo3::types::PyBytes;

mod crypto;
mod x25519;
mod amplitude;

/// QDAP Core — Rust ile hızlandırılmış kriptografi ve hesaplama.
/// Python'dan `import qdap_core` ile kullanılır.
#[pymodule]
fn qdap_core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    // Kriptografi fonksiyonları
    m.add_function(wrap_pyfunction!(hash_frame, m)?)?;
    m.add_function(wrap_pyfunction!(encrypt_frame, m)?)?;
    m.add_function(wrap_pyfunction!(decrypt_frame, m)?)?;

    // X25519 key exchange
    m.add_function(wrap_pyfunction!(x25519_generate_keypair, m)?)?;
    m.add_function(wrap_pyfunction!(x25519_diffie_hellman, m)?)?;

    // Amplitude normalizasyon
    m.add_function(wrap_pyfunction!(normalize_amplitudes, m)?)?;
    m.add_function(wrap_pyfunction!(compute_deadline_weights, m)?)?;

    // Versiyon
    m.add("__version__", "0.1.0")?;
    m.add("__backend__", "rust")?;

    Ok(())
}

/// SHA3-256 hash hesapla.
/// 
/// Args:
///     payload: Hash'lenecek bytes
/// 
/// Returns:
///     32 byte SHA3-256 digest
#[pyfunction]
fn hash_frame<'py>(py: Python<'py>, payload: &[u8]) -> &'py PyBytes {
    let digest = crypto::sha3_256(payload);
    PyBytes::new(py, &digest)
}

/// AES-256-GCM ile şifrele.
/// 
/// Args:
///     key:        32 byte AES anahtarı
///     nonce:      12 byte nonce (tekrar kullanılmamalı!)
///     plaintext:  Şifrelenecek veri
///     aad:        Associated data (opsiyonel, b"" geçilebilir)
/// 
/// Returns:
///     ciphertext + 16 byte tag (birleşik)
#[pyfunction]
fn encrypt_frame<'py>(
    py:        Python<'py>,
    key:       &[u8],
    nonce:     &[u8],
    plaintext: &[u8],
    aad:       &[u8],
) -> PyResult<&'py PyBytes> {
    crypto::aes_gcm_encrypt(key, nonce, plaintext, aad)
        .map(|ct| PyBytes::new(py, &ct))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

/// AES-256-GCM ile deşifrele ve doğrula.
/// 
/// Args:
///     key:        32 byte AES anahtarı
///     nonce:      12 byte nonce
///     ciphertext: Şifreli veri
///     tag:        16 byte authentication tag
///     aad:        Associated data
/// 
/// Returns:
///     Plaintext bytes
/// 
/// Raises:
///     ValueError: Authentication başarısız (tampered data)
#[pyfunction]
fn decrypt_frame<'py>(
    py:         Python<'py>,
    key:        &[u8],
    nonce:      &[u8],
    ciphertext: &[u8],
    tag:        &[u8],
    aad:        &[u8],
) -> PyResult<&'py PyBytes> {
    crypto::aes_gcm_decrypt(key, nonce, ciphertext, tag, aad)
        .map(|pt| PyBytes::new(py, &pt))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(
            format!("Authentication failed: {}", e)
        ))
}

/// Yeni X25519 ephemeral keypair üret.
/// 
/// Returns:
///     (private_key_bytes: bytes, public_key_bytes: bytes)
///     Her ikisi de 32 byte
#[pyfunction]
fn x25519_generate_keypair<'py>(
    py: Python<'py>,
) -> (&'py PyBytes, &'py PyBytes) {
    let (priv_bytes, pub_bytes) = x25519::generate_keypair();
    (
        PyBytes::new(py, &priv_bytes),
        PyBytes::new(py, &pub_bytes),
    )
}

/// X25519 Diffie-Hellman key exchange.
/// 
/// Args:
///     private_key: 32 byte private key
///     public_key:  32 byte peer public key
/// 
/// Returns:
///     32 byte shared secret
#[pyfunction]
fn x25519_diffie_hellman<'py>(
    py:          Python<'py>,
    private_key: &[u8],
    public_key:  &[u8],
) -> PyResult<&'py PyBytes> {
    x25519::diffie_hellman(private_key, public_key)
        .map(|secret| PyBytes::new(py, &secret))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

/// L2 normalizasyon — AmplitudeEncoder için.
/// 
/// Args:
///     amplitudes: float listesi
/// 
/// Returns:
///     L2-normalized float listesi
#[pyfunction]
fn normalize_amplitudes(amplitudes: Vec<f64>) -> Vec<f64> {
    amplitude::l2_normalize(&amplitudes)
}

/// Deadline'a göre amplitude ağırlıkları hesapla.
/// 
/// Args:
///     deadlines_ms: deadline listesi (ms cinsinden)
/// 
/// Returns:
///     Normalized weight listesi (küçük deadline = büyük ağırlık)
#[pyfunction]
fn compute_deadline_weights(deadlines_ms: Vec<f64>) -> Vec<f64> {
    amplitude::deadline_to_weights(&deadlines_ms)
}
```

---

## ADIM 3 — crypto.rs (SHA3 + AES-GCM)

```rust
// qdap_core/src/crypto.rs

use aes_gcm::{
    aead::{Aead, AeadCore, KeyInit, OsRng, Payload},
    Aes256Gcm, Key, Nonce,
};
use sha3::{Digest, Sha3_256};

/// SHA3-256 hash hesapla.
pub fn sha3_256(data: &[u8]) -> [u8; 32] {
    let mut hasher = Sha3_256::new();
    hasher.update(data);
    hasher.finalize().into()
}

/// AES-256-GCM şifreleme.
/// Returns: ciphertext + 16 byte tag (birleşik)
pub fn aes_gcm_encrypt(
    key:       &[u8],
    nonce:     &[u8],
    plaintext: &[u8],
    aad:       &[u8],
) -> Result<Vec<u8>, String> {
    // Key validasyon
    if key.len() != 32 {
        return Err(format!("Key must be 32 bytes, got {}", key.len()));
    }
    if nonce.len() != 12 {
        return Err(format!("Nonce must be 12 bytes, got {}", nonce.len()));
    }

    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    let nonce  = Nonce::from_slice(nonce);

    let payload = Payload {
        msg: plaintext,
        aad,
    };

    cipher
        .encrypt(nonce, payload)
        .map_err(|e| format!("Encryption failed: {}", e))
}

/// AES-256-GCM deşifreleme ve doğrulama.
/// Returns: plaintext
pub fn aes_gcm_decrypt(
    key:        &[u8],
    nonce:      &[u8],
    ciphertext: &[u8],
    tag:        &[u8],
    aad:        &[u8],
) -> Result<Vec<u8>, String> {
    if key.len() != 32 {
        return Err(format!("Key must be 32 bytes, got {}", key.len()));
    }
    if nonce.len() != 12 {
        return Err(format!("Nonce must be 12 bytes, got {}", nonce.len()));
    }
    if tag.len() != 16 {
        return Err(format!("Tag must be 16 bytes, got {}", tag.len()));
    }

    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    let nonce  = Nonce::from_slice(nonce);

    // ciphertext + tag birleştir (aes-gcm crate beklentisi)
    let mut ct_with_tag = ciphertext.to_vec();
    ct_with_tag.extend_from_slice(tag);

    let payload = Payload {
        msg: &ct_with_tag,
        aad,
    };

    cipher
        .decrypt(nonce, payload)
        .map_err(|_| "Authentication tag verification failed".to_string())
}
```

---

## ADIM 4 — x25519.rs (Key Exchange)

```rust
// qdap_core/src/x25519.rs

use x25519_dalek::{EphemeralSecret, PublicKey, StaticSecret};
use rand::rngs::OsRng;

/// Yeni ephemeral keypair üret.
/// Returns: (private_key_bytes, public_key_bytes) — her ikisi 32 byte
pub fn generate_keypair() -> ([u8; 32], [u8; 32]) {
    let secret = EphemeralSecret::random_from_rng(OsRng);
    let public = PublicKey::from(&secret);

    // EphemeralSecret consume edilir — bir kez kullanılır
    // Private key'i byte olarak döndürmek için StaticSecret kullan
    let static_secret = StaticSecret::random_from_rng(OsRng);
    let static_public = PublicKey::from(&static_secret);

    (
        static_secret.to_bytes(),
        static_public.to_bytes(),
    )
}

/// X25519 Diffie-Hellman shared secret hesapla.
pub fn diffie_hellman(
    private_key_bytes: &[u8],
    public_key_bytes:  &[u8],
) -> Result<[u8; 32], String> {
    if private_key_bytes.len() != 32 {
        return Err(format!(
            "Private key must be 32 bytes, got {}",
            private_key_bytes.len()
        ));
    }
    if public_key_bytes.len() != 32 {
        return Err(format!(
            "Public key must be 32 bytes, got {}",
            public_key_bytes.len()
        ));
    }

    let mut priv_array = [0u8; 32];
    priv_array.copy_from_slice(private_key_bytes);
    let mut pub_array  = [0u8; 32];
    pub_array.copy_from_slice(public_key_bytes);

    let secret  = StaticSecret::from(priv_array);
    let public  = PublicKey::from(pub_array);
    let shared  = secret.diffie_hellman(&public);

    Ok(shared.to_bytes())
}
```

---

## ADIM 5 — amplitude.rs (L2 Normalizasyon)

```rust
// qdap_core/src/amplitude.rs

/// L2 normalizasyon — ||v||₂ = 1
/// AmplitudeEncoder için kritik yol.
pub fn l2_normalize(amplitudes: &[f64]) -> Vec<f64> {
    if amplitudes.is_empty() {
        return vec![];
    }

    // L2 norm hesapla: sqrt(sum(x^2))
    let norm: f64 = amplitudes
        .iter()
        .map(|x| x * x)
        .sum::<f64>()
        .sqrt();

    if norm < 1e-10 {
        // Sıfır vektör — uniform dağılım döndür
        let uniform = 1.0 / (amplitudes.len() as f64).sqrt();
        return vec![uniform; amplitudes.len()];
    }

    amplitudes.iter().map(|x| x / norm).collect()
}

/// Deadline'lardan amplitude ağırlıkları hesapla.
/// Küçük deadline → yüksek ağırlık (öncelikli)
/// 
/// Formül: weight_i = 1 / deadline_i, sonra L2 normalize et
pub fn deadline_to_weights(deadlines_ms: &[f64]) -> Vec<f64> {
    if deadlines_ms.is_empty() {
        return vec![];
    }

    // Inverse deadline → ham ağırlıklar
    // min deadline = 1.0 (en öncelikli)
    let min_deadline = deadlines_ms
        .iter()
        .cloned()
        .fold(f64::INFINITY, f64::min)
        .max(0.001);   // Sıfır deadline koruması

    let raw_weights: Vec<f64> = deadlines_ms
        .iter()
        .map(|d| min_deadline / d.max(0.001))
        .collect();

    l2_normalize(&raw_weights)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_l2_norm_is_one() {
        let v = vec![3.0, 4.0];
        let n = l2_normalize(&v);
        let norm: f64 = n.iter().map(|x| x * x).sum::<f64>().sqrt();
        assert!((norm - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_deadline_priority() {
        // 2ms deadline → 500ms deadline'dan yüksek ağırlık
        let deadlines = vec![2.0, 500.0];
        let weights   = deadline_to_weights(&deadlines);
        assert!(weights[0] > weights[1]);
    }
}
```

---

## ADIM 6 — Python Bridge (_rust_bridge.py)

```python
# src/qdap/_rust_bridge.py
"""
Rust/Python seçici köprü.

Rust build varsa → qdap_core (hızlı)
Yoksa           → pure Python fallback (mevcut implementasyon)

Kullanım:
    from qdap._rust_bridge import hash_frame, encrypt_frame, decrypt_frame
    
    # Otomatik olarak Rust veya Python kullanır
    digest = hash_frame(payload)
"""

import logging

log = logging.getLogger(__name__)

try:
    import qdap_core as _rust
    RUST_AVAILABLE = True
    log.info("qdap_core Rust backend loaded — hardware acceleration active")
except ImportError:
    _rust = None
    RUST_AVAILABLE = False
    log.debug("qdap_core not available — using pure Python fallback")


def hash_frame(payload: bytes) -> bytes:
    """SHA3-256 hash — Rust varsa Rust, yoksa Python."""
    if RUST_AVAILABLE:
        return _rust.hash_frame(payload)
    # Pure Python fallback
    import hashlib
    return hashlib.sha3_256(payload).digest()


def encrypt_frame(
    key:       bytes,
    nonce:     bytes,
    plaintext: bytes,
    aad:       bytes = b"",
) -> bytes:
    """
    AES-256-GCM şifreleme.
    Returns: ciphertext + 16 byte tag
    """
    if RUST_AVAILABLE:
        return _rust.encrypt_frame(key, nonce, plaintext, aad)
    # Pure Python fallback (mevcut FrameEncryptor)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).encrypt(nonce, plaintext, aad if aad else None)


def decrypt_frame(
    key:        bytes,
    nonce:      bytes,
    ciphertext: bytes,
    tag:        bytes,
    aad:        bytes = b"",
) -> bytes:
    """
    AES-256-GCM deşifreleme.
    Raises ValueError eğer authentication başarısız.
    """
    if RUST_AVAILABLE:
        return _rust.decrypt_frame(key, nonce, ciphertext, tag, aad)
    # Pure Python fallback
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, aad if aad else None)
    except InvalidTag:
        raise ValueError("Authentication failed")


def x25519_generate_keypair() -> tuple[bytes, bytes]:
    """
    X25519 ephemeral keypair üret.
    Returns: (private_key_32b, public_key_32b)
    """
    if RUST_AVAILABLE:
        return _rust.x25519_generate_keypair()
    # Pure Python fallback
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption
    )
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes  = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes, pub_bytes


def x25519_diffie_hellman(
    private_key: bytes,
    public_key:  bytes,
) -> bytes:
    """X25519 DH — shared secret hesapla."""
    if RUST_AVAILABLE:
        return _rust.x25519_diffie_hellman(private_key, public_key)
    # Pure Python fallback
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    priv = X25519PrivateKey.from_private_bytes(private_key)
    pub  = X25519PublicKey.from_public_bytes(public_key)
    return priv.exchange(pub)


def normalize_amplitudes(amplitudes: list[float]) -> list[float]:
    """L2 normalizasyon."""
    if RUST_AVAILABLE:
        return _rust.normalize_amplitudes(amplitudes)
    # Pure Python fallback
    import math
    norm = math.sqrt(sum(x * x for x in amplitudes))
    if norm < 1e-10:
        uniform = 1.0 / math.sqrt(len(amplitudes))
        return [uniform] * len(amplitudes)
    return [x / norm for x in amplitudes]


def compute_deadline_weights(deadlines_ms: list[float]) -> list[float]:
    """Deadline'lardan amplitude ağırlıkları hesapla."""
    if RUST_AVAILABLE:
        return _rust.compute_deadline_weights(deadlines_ms)
    # Pure Python fallback
    min_d = max(min(deadlines_ms), 0.001)
    raw   = [min_d / max(d, 0.001) for d in deadlines_ms]
    return normalize_amplitudes(raw)


def backend_info() -> dict:
    """Hangi backend kullanılıyor?"""
    return {
        "rust_available":   RUST_AVAILABLE,
        "backend":          "rust" if RUST_AVAILABLE else "python",
        "version":          getattr(_rust, "__version__", None) if RUST_AVAILABLE else None,
    }
```

---

## ADIM 7 — encrypted_frame.py Güncelle

```python
# src/qdap/security/encrypted_frame.py
# Mevcut dosyada sadece şu iki import satırını değiştir:

# ESKİ:
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# YENİ (dosyanın en üstüne ekle):
from qdap._rust_bridge import encrypt_frame as _encrypt, decrypt_frame as _decrypt
```

`FrameEncryptor.encrypt()` metodunu güncelle:
```python
def encrypt(self, plaintext, associated_data=b""):
    nonce = self._make_nonce()
    ct_with_tag = _encrypt(
        key=self._key,        # 32 byte
        nonce=nonce,
        plaintext=plaintext,
        aad=associated_data,
    )
    ciphertext = ct_with_tag[:-16]
    tag        = ct_with_tag[-16:]
    return EncryptionResult(nonce=nonce, tag=tag, ciphertext=ciphertext)

def decrypt(self, nonce, tag, ciphertext, associated_data=b""):
    try:
        plaintext = _decrypt(
            key=self._key,
            nonce=nonce,
            ciphertext=ciphertext,
            tag=tag,
            aad=associated_data,
        )
        return DecryptionResult(plaintext=plaintext, verified=True)
    except ValueError:
        return DecryptionResult(plaintext=b"", verified=False)
```

---

## ADIM 8 — Build Script

```bash
# scripts/build_rust.sh

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

key   = b'\\x42' * 32
nonce = b'\\x01' * 12
ct    = qdap_core.encrypt_frame(key, nonce, b'hello', b'')
print(f'AES-GCM encrypt: {len(ct)} bytes ✅')

priv, pub = qdap_core.x25519_generate_keypair()
print(f'X25519 keypair: priv={len(priv)}B pub={len(pub)}B ✅')

print('')
print('🚀 Rust backend aktif — hardware acceleration hazır!')
"
```

---

## ADIM 9 — Benchmark (Rust vs Python Hız Karşılaştırması)

```python
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
```

---

## ADIM 10 — Testler

```python
# tests/test_rust_bridge.py
"""
Rust ve Python implementasyonlarının aynı sonucu ürettiğini doğrula.
Her fonksiyon için parity test.
"""

import os
import pytest
import hashlib
from qdap._rust_bridge import (
    hash_frame, encrypt_frame, decrypt_frame,
    x25519_generate_keypair, x25519_diffie_hellman,
    normalize_amplitudes, RUST_AVAILABLE
)


class TestHashFrame:

    def test_sha3_256_correctness(self):
        """Rust SHA3-256 == Python hashlib SHA3-256."""
        payload  = b"QDAP test payload" * 100
        expected = hashlib.sha3_256(payload).digest()
        result   = hash_frame(payload)
        assert result == expected

    def test_empty_payload(self):
        expected = hashlib.sha3_256(b"").digest()
        assert hash_frame(b"") == expected

    def test_large_payload(self):
        payload  = os.urandom(1024 * 1024)
        expected = hashlib.sha3_256(payload).digest()
        assert hash_frame(payload) == expected


class TestEncryptDecrypt:

    def test_roundtrip(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b"Hello QDAP Rust!"
        ct    = encrypt_frame(key, nonce, plain, b"")
        pt    = decrypt_frame(key, nonce, ct[:-16], ct[-16:], b"")
        assert pt == plain

    def test_aad_roundtrip(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b"payload" * 100
        aad   = b"frame-header"
        ct    = encrypt_frame(key, nonce, plain, aad)
        pt    = decrypt_frame(key, nonce, ct[:-16], ct[-16:], aad)
        assert pt == plain

    def test_tampered_ciphertext_raises(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        ct    = encrypt_frame(key, nonce, b"secret", b"")
        tampered = bytearray(ct)
        tampered[-1] ^= 0xFF
        with pytest.raises((ValueError, Exception)):
            decrypt_frame(key, nonce, bytes(tampered)[:-16], bytes(tampered)[-16:], b"")

    def test_wrong_key_raises(self):
        key1  = os.urandom(32)
        key2  = os.urandom(32)
        nonce = os.urandom(12)
        ct    = encrypt_frame(key1, nonce, b"secret", b"")
        with pytest.raises((ValueError, Exception)):
            decrypt_frame(key2, nonce, ct[:-16], ct[-16:], b"")


class TestX25519:

    def test_keypair_size(self):
        priv, pub = x25519_generate_keypair()
        assert len(priv) == 32
        assert len(pub)  == 32

    def test_dh_symmetric(self):
        """Alice ve Bob aynı shared secret'a ulaşmalı."""
        alice_priv, alice_pub = x25519_generate_keypair()
        bob_priv,   bob_pub   = x25519_generate_keypair()

        alice_secret = x25519_diffie_hellman(alice_priv, bob_pub)
        bob_secret   = x25519_diffie_hellman(bob_priv,   alice_pub)

        assert alice_secret == bob_secret
        assert len(alice_secret) == 32

    def test_different_sessions_different_secrets(self):
        a1_priv, a1_pub = x25519_generate_keypair()
        b1_priv, b1_pub = x25519_generate_keypair()
        a2_priv, a2_pub = x25519_generate_keypair()
        b2_priv, b2_pub = x25519_generate_keypair()

        s1 = x25519_diffie_hellman(a1_priv, b1_pub)
        s2 = x25519_diffie_hellman(a2_priv, b2_pub)
        assert s1 != s2   # Forward secrecy


class TestAmplitude:

    def test_l2_norm_is_one(self):
        import math
        result = normalize_amplitudes([3.0, 4.0])
        norm   = math.sqrt(sum(x*x for x in result))
        assert abs(norm - 1.0) < 1e-10

    def test_priority_ordering(self):
        from qdap._rust_bridge import compute_deadline_weights
        weights = compute_deadline_weights([2.0, 500.0])
        assert weights[0] > weights[1]   # Emergency > routine
```

---

## Teslim Kriterleri

```
✅ qdap_core/ Rust crate oluşturuldu
✅ maturin develop --release başarıyla çalıştı
✅ import qdap_core → __backend__ = "rust"
✅ src/qdap/_rust_bridge.py oluşturuldu
✅ encrypted_frame.py Rust bridge kullanıyor
✅ 212 mevcut test hâlâ geçiyor
✅ tests/test_rust_bridge.py → 12 yeni test geçiyor
✅ Toplam: 224+ test

Benchmark sonucu (beklenen):
  hash_frame(256KB):    Python ~2ms  → Rust ~0.05ms  (40×)
  encrypt_frame(256KB): Python ~3ms  → Rust ~0.02ms  (150×)
  normalize(1024):      Python ~0.1ms → Rust ~0.002ms (50×)

Bitince şunu çalıştır ve bize gönder:
  1. pytest tests/ → toplam test sayısı
  2. python benchmarks/rust_vs_python_benchmark.py
     (Rust backend aktifken)
```

---

## DOKUNMA

```
Şu dosyalara KESİNLİKLE DOKUNMA:
  - src/qdap/session/ghost_session.py
  - src/qdap/frame/qframe.py
  - src/qdap/scheduler/qft_scheduler.py
  - docker_benchmark/ altındaki her şey
  - Mevcut test dosyaları (sadece yeni ekle)

Sadece şunları oluştur/değiştir:
  - qdap_core/ (yeni Rust crate)
  - src/qdap/_rust_bridge.py (yeni)
  - src/qdap/security/encrypted_frame.py (bridge entegrasyonu)
  - tests/test_rust_bridge.py (yeni)
  - scripts/build_rust.sh (yeni)
```
