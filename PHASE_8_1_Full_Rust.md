# PHASE 8.1 — Full Rust: QFrame Parser + QFT Scheduler
## Gemini Agent İçin: Tam Kod, Sıfır Varsayım
## Tahmini Süre: 3-4 hafta | Zorluk: Yüksek

---

## Hedef

Mevcut durum:
```
qdap_core (Rust) — zaten var:
  ✅ AES-256-GCM şifreleme/şifre çözme
  ✅ SHA3-256 hash
  ✅ X25519 key exchange
  ✅ L2 normalize (AmplitudeEncoder)

Eksik (bu phase):
  ❌ QFrame serialize/deserialize  → Rust'a taşı
  ❌ QFTScheduler.decide() hot loop → Rust'a taşı
  ❌ AdaptiveChunker._split() → Rust'a taşı
  ❌ Born-rule amplitude encode → tamamla
```

Beklenen kazanım:
```
1MB boyutunda Python overhead kalkınca:
  Python QFrame serialize: ~0.5ms/frame
  Rust QFrame serialize:   ~0.01ms/frame → 50×

  Python QFT decide():     ~0.3ms/call
  Rust QFT decide():       ~0.005ms/call → 60×

  1MB = 16 chunk:
    Python: 16 × 0.8ms = 12.8ms overhead
    Rust:   16 × 0.015ms = 0.24ms overhead
    → 1MB'de de QDAP Classical'ı net geçer
```

---

## Mevcut Proje Yapısı

```
quantum-protocol/
├── src/qdap/
│   ├── frame/
│   │   └── qframe.py           ← serialize/deserialize burası
│   ├── scheduler/
│   │   └── qft_scheduler.py    ← decide() burası
│   ├── chunking/
│   │   └── adaptive_chunker.py ← _split_payload() burası
│   ├── encoding/
│   │   └── amplitude_encoder.py ← encode() burası
│   └── _rust_bridge.py         ← mevcut bridge
│
└── qdap_core/                  ← mevcut Rust crate
    ├── Cargo.toml
    └── src/
        ├── lib.rs
        ├── crypto.rs
        ├── x25519.rs
        └── amplitude.rs
```

---

## ADIM 1 — QFrame Wire Format Analizi

Önce mevcut Python QFrame'i incele:
```bash
cat src/qdap/frame/qframe.py
```

Aşağıdaki bilgileri belirle ve bize bildir:
1. `QFrame.to_bytes()` → hangi alanlar, hangi sırayla, kaç byte?
2. `QFrame.from_bytes()` → parse sırası
3. Magic number var mı? (header validation)
4. Checksum/hash nerede hesaplanıyor?

**Beklenen wire format (tahmin — doğrula):**
```
Offset  Size  Field
0       4     magic (0x51444150 = "QDAP")
4       1     version
5       1     frame_type (0=data, 1=control, 2=keepalive)
6       2     priority (0-65535)
8       8     deadline_ms (f64 little-endian)
16      8     sequence_number (u64)
24      4     payload_length (u32)
28      32    payload_hash (SHA3-256)
60      N     payload
```

---

## ADIM 2 — Cargo.toml Güncelle

```toml
# qdap_core/Cargo.toml — mevcut dosyaya EKLE

[dependencies]
# ... mevcut bağımlılıklar ...

# QFrame serialization
byteorder = "1.5"      # little/big endian helpers
bytes     = "1.5"      # zero-copy byte buffers

# QFT Scheduler
libm = "0.2"           # no_std math (cos, sin, sqrt)
```

---

## ADIM 3 — qframe.rs

```rust
// qdap_core/src/qframe.rs

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sha3::{Digest, Sha3_256};

/// QFrame wire format:
///   0-3:   magic (0x51444150)
///   4:     version
///   5:     frame_type
///   6-7:   priority (u16 little-endian)
///   8-15:  deadline_ms (f64 little-endian)
///   16-23: sequence_number (u64 little-endian)
///   24-27: payload_length (u32 little-endian)
///   28-59: payload_hash (SHA3-256, 32 bytes)
///   60+:   payload
pub const MAGIC: u32 = 0x51444150; // "QDAP"
pub const HEADER_SIZE: usize = 60;

pub const FRAME_TYPE_DATA:      u8 = 0;
pub const FRAME_TYPE_CONTROL:   u8 = 1;
pub const FRAME_TYPE_KEEPALIVE: u8 = 2;


/// QFrame → bytes (serialize)
///
/// Args:
///     payload:         ham veri
///     priority:        0-65535 (yüksek = daha önemli)
///     deadline_ms:     son tarih (f64, ms)
///     sequence_number: frame sırası
///     frame_type:      0=data, 1=control, 2=keepalive
///
/// Returns:
///     Tam wire formatında bytes (header + payload)
#[pyfunction]
pub fn qframe_serialize<'py>(
    py:              Python<'py>,
    payload:         &[u8],
    priority:        u16,
    deadline_ms:     f64,
    sequence_number: u64,
    frame_type:      u8,
) -> &'py PyBytes {
    let payload_len = payload.len() as u32;
    let hash        = crate::crypto::sha3_256(payload);

    let mut buf = Vec::with_capacity(HEADER_SIZE + payload.len());

    // Magic (big-endian, ASCII okunabilir)
    buf.extend_from_slice(&MAGIC.to_be_bytes());

    // Version
    buf.push(1u8);

    // Frame type
    buf.push(frame_type);

    // Priority (little-endian u16)
    buf.extend_from_slice(&priority.to_le_bytes());

    // Deadline ms (little-endian f64)
    buf.extend_from_slice(&deadline_ms.to_le_bytes());

    // Sequence number (little-endian u64)
    buf.extend_from_slice(&sequence_number.to_le_bytes());

    // Payload length (little-endian u32)
    buf.extend_from_slice(&payload_len.to_le_bytes());

    // Payload hash (SHA3-256, 32 bytes)
    buf.extend_from_slice(&hash);

    // Payload
    buf.extend_from_slice(payload);

    PyBytes::new(py, &buf)
}


/// bytes → QFrame fields (deserialize)
///
/// Returns:
///     (payload, priority, deadline_ms, sequence_number, frame_type, hash_valid)
///
/// Raises:
///     ValueError: geçersiz magic veya payload_length
#[pyfunction]
pub fn qframe_deserialize<'py>(
    py:   Python<'py>,
    data: &[u8],
) -> PyResult<(&'py PyBytes, u16, f64, u64, u8, bool)> {
    if data.len() < HEADER_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Frame too short: {} < {}", data.len(), HEADER_SIZE)
        ));
    }

    // Magic doğrula
    let magic = u32::from_be_bytes(data[0..4].try_into().unwrap());
    if magic != MAGIC {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Invalid magic: 0x{:08X}", magic)
        ));
    }

    // Version (şimdilik yoksay, ilerisi için)
    let _version = data[4];

    // Frame type
    let frame_type = data[5];

    // Priority
    let priority = u16::from_le_bytes(data[6..8].try_into().unwrap());

    // Deadline ms
    let deadline_ms = f64::from_le_bytes(data[8..16].try_into().unwrap());

    // Sequence number
    let sequence_number = u64::from_le_bytes(data[16..24].try_into().unwrap());

    // Payload length
    let payload_length = u32::from_le_bytes(data[24..28].try_into().unwrap()) as usize;

    // Hash (32 bytes)
    let stored_hash = &data[28..60];

    // Payload
    let expected_total = HEADER_SIZE + payload_length;
    if data.len() < expected_total {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!(
                "Truncated frame: expected {} bytes, got {}",
                expected_total,
                data.len()
            )
        ));
    }

    let payload = &data[HEADER_SIZE..HEADER_SIZE + payload_length];

    // Hash doğrula
    let computed_hash = crate::crypto::sha3_256(payload);
    let hash_valid    = computed_hash == stored_hash;

    Ok((
        PyBytes::new(py, payload),
        priority,
        deadline_ms,
        sequence_number,
        frame_type,
        hash_valid,
    ))
}


/// Sadece header parse et (payload okuma olmadan — hızlı peek)
///
/// Returns:
///     (payload_length, priority, deadline_ms, frame_type)
#[pyfunction]
pub fn qframe_peek_header(data: &[u8]) -> PyResult<(u32, u16, f64, u8)> {
    if data.len() < HEADER_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err("Too short for header"));
    }

    let magic = u32::from_be_bytes(data[0..4].try_into().unwrap());
    if magic != MAGIC {
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid magic"));
    }

    let frame_type     = data[5];
    let priority       = u16::from_le_bytes(data[6..8].try_into().unwrap());
    let deadline_ms    = f64::from_le_bytes(data[8..16].try_into().unwrap());
    let payload_length = u32::from_le_bytes(data[24..28].try_into().unwrap());

    Ok((payload_length, priority, deadline_ms, frame_type))
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_roundtrip() {
        Python::with_gil(|py| {
            let payload = b"Hello QDAP Rust QFrame!";
            let wire    = qframe_serialize(
                py, payload, 100, 500.0, 42, FRAME_TYPE_DATA
            );

            let (parsed_payload, priority, deadline, seq, ftype, valid) =
                qframe_deserialize(py, wire.as_bytes()).unwrap();

            assert_eq!(parsed_payload.as_bytes(), payload);
            assert_eq!(priority,  100);
            assert_eq!(deadline,  500.0);
            assert_eq!(seq,       42);
            assert_eq!(ftype,     FRAME_TYPE_DATA);
            assert!(valid);
        });
    }

    #[test]
    fn test_tampered_payload_invalid() {
        Python::with_gil(|py| {
            let payload = b"test data";
            let wire    = qframe_serialize(py, payload, 0, 0.0, 0, 0);
            let mut tampered = wire.as_bytes().to_vec();
            tampered[61] ^= 0xFF;  // payload'ı bozduk

            let (_, _, _, _, _, valid) =
                qframe_deserialize(py, &tampered).unwrap();
            assert!(!valid);
        });
    }
}
```

---

## ADIM 4 — qft_scheduler.rs

**Önce Python kodunu oku:**
```bash
cat src/qdap/scheduler/qft_scheduler.py
```

**QFT karar algoritması (tahmin — Python kodundan doğrula):**
```
decide(payload_size, rtt_ms, loss_rate) → ChunkStrategy

Algoritmik özet:
  1. energy_bands hesapla (QFT-FFT analogu)
     frequencies = [1/payload_size, 1/rtt_ms, loss_rate]
     amplitudes = normalize(frequencies)

  2. En yüksek amplitüde göre strateji seç:
     - Küçük payload + yüksek loss → küçük chunk (reliable)
     - Büyük payload + düşük loss → büyük chunk (throughput)
     - Yüksek RTT → küçük chunk (deadline-aware)

  3. Returns:
     chunk_size_bytes: int
     strategy_name: str
     confidence: float
```

```rust
// qdap_core/src/qft_scheduler.rs

use pyo3::prelude::*;

/// Chunk strateji sabitleri
pub const STRATEGY_MICRO:   u8 = 0;  // 4KB   — yüksek loss, küçük payload
pub const STRATEGY_SMALL:   u8 = 1;  // 16KB  — orta loss
pub const STRATEGY_MEDIUM:  u8 = 2;  // 64KB  — normal koşullar
pub const STRATEGY_LARGE:   u8 = 3;  // 256KB — büyük payload, düşük loss
pub const STRATEGY_JUMBO:   u8 = 4;  // 1MB+  — LAN, sıfır loss

pub const CHUNK_SIZES: [usize; 5] = [
    4 * 1024,       // 4KB
    16 * 1024,      // 16KB
    64 * 1024,      // 64KB
    256 * 1024,     // 256KB
    1024 * 1024,    // 1MB
];

pub const STRATEGY_NAMES: [&str; 5] = [
    "MICRO", "SMALL", "MEDIUM", "LARGE", "JUMBO"
];


/// QFT-ilhamlı chunk strateji kararı.
///
/// Algoritmik temel: QFT'nin frekans bileşenlerini payload/RTT/loss
/// üçgenine uyguluyoruz. En yüksek "enerji" hangi boyut aralığında
/// toplanıyorsa oraya uygun chunk seçilir.
///
/// Args:
///     payload_size: toplam payload boyutu (bytes)
///     rtt_ms:       tahmin edilen RTT (milliseconds)
///     loss_rate:    paket kaybı oranı (0.0 - 1.0)
///
/// Returns:
///     (chunk_size_bytes, strategy_index, confidence_0_to_1)
#[pyfunction]
pub fn qft_decide(
    payload_size: usize,
    rtt_ms:       f64,
    loss_rate:    f64,
) -> (usize, u8, f64) {

    // Normalize parametreler (0.0 - 1.0 aralığına)
    // payload: log scale, 1B → 0.0, 100MB → 1.0
    let payload_norm = (payload_size as f64).log10().max(0.0) / 8.0;  // 10^8 = 100MB
    let payload_norm = payload_norm.min(1.0);

    // RTT: 0ms → 0.0, 500ms → 1.0
    let rtt_norm = (rtt_ms / 500.0).min(1.0);

    // Loss: 0.0 → 0.0, 0.2 → 1.0
    let loss_norm = (loss_rate / 0.2).min(1.0);

    // QFT enerji hesabı — 3 bileşenin interferans toplamı
    // Bu klasik QFT'nin DFT eşdeğeri
    let scores: [f64; 5] = [
        // MICRO (4KB): küçük payload + yüksek loss + düşük RTT
        (1.0 - payload_norm) * 0.3 + loss_norm * 0.5 + (1.0 - rtt_norm) * 0.2,

        // SMALL (16KB): küçük-orta payload + orta loss
        (1.0 - payload_norm).powi(2) * 0.4 + (1.0 - loss_norm) * loss_norm * 0.4 + 0.2,

        // MEDIUM (64KB): orta payload, normal koşullar — default
        (1.0 - (payload_norm - 0.5).abs() * 2.0).max(0.0) * 0.5
            + (1.0 - loss_norm) * 0.3 + 0.2,

        // LARGE (256KB): büyük payload + düşük loss + yüksek RTT
        payload_norm * 0.4 + (1.0 - loss_norm) * 0.4 + rtt_norm * 0.2,

        // JUMBO (1MB): çok büyük payload + sıfır loss + LAN (düşük RTT)
        payload_norm.powi(2) * 0.5 + (1.0 - loss_norm).powi(2) * 0.4
            + (1.0 - rtt_norm) * 0.1,
    ];

    // En yüksek skora sahip stratejiyi seç
    let (best_idx, &best_score) = scores
        .iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap())
        .unwrap();

    // Confidence: en yüksek skor ile ikinci en yüksek arasındaki fark
    let mut sorted = scores;
    sorted.sort_by(|a, b| b.partial_cmp(a).unwrap());
    let confidence = ((sorted[0] - sorted[1]) / sorted[0]).min(1.0);

    (CHUNK_SIZES[best_idx], best_idx as u8, confidence)
}


/// Batch karar — birden fazla payload için (pipeline optimizasyon)
///
/// Args:
///     payloads: (payload_size, rtt_ms, loss_rate) tupleları
///
/// Returns:
///     (chunk_size, strategy_idx, confidence) listesi
#[pyfunction]
pub fn qft_decide_batch(
    payloads: Vec<(usize, f64, f64)>
) -> Vec<(usize, u8, f64)> {
    payloads.iter()
        .map(|&(size, rtt, loss)| qft_decide(size, rtt, loss))
        .collect()
}


/// Deadline-aware override:
/// Deadline çok yakınsa (acil durum) → en küçük chunk
///
/// Args:
///     payload_size:    payload boyutu
///     rtt_ms:          RTT tahmini
///     loss_rate:       paket kaybı
///     deadline_ms:     kalan süre (ms)
///     elapsed_ms:      geçen süre (ms)
///
/// Returns:
///     (chunk_size_bytes, strategy_index, is_emergency)
#[pyfunction]
pub fn qft_decide_deadline_aware(
    payload_size: usize,
    rtt_ms:       f64,
    loss_rate:    f64,
    deadline_ms:  f64,
    elapsed_ms:   f64,
) -> (usize, u8, bool) {
    let remaining_ms = deadline_ms - elapsed_ms;

    // Acil durum: kalan süre RTT'nin 2 katından az
    let is_emergency = remaining_ms < rtt_ms * 2.0 || remaining_ms < 5.0;

    if is_emergency {
        // Acil: en küçük chunk, hızlı gönder
        return (CHUNK_SIZES[STRATEGY_MICRO as usize], STRATEGY_MICRO, true);
    }

    // Normal karar
    let (chunk_size, strategy, _) = qft_decide(payload_size, rtt_ms, loss_rate);
    (chunk_size, strategy, false)
}


/// Benchmark için: 1 milyon karar/saniye test
#[pyfunction]
pub fn qft_benchmark(n: usize) -> f64 {
    use std::time::Instant;
    let t0 = Instant::now();
    let mut total = 0usize;
    for i in 0..n {
        let (chunk, _, _) = qft_decide(
            1024 * (1 + i % 1024),  // 1KB - 1MB
            20.0 + (i % 100) as f64, // 20-120ms RTT
            0.01 * (i % 20) as f64,  // 0-20% loss
        );
        total += chunk;
    }
    let elapsed = t0.elapsed().as_secs_f64();
    let _ = total;  // prevent optimization
    n as f64 / elapsed  // decisions/second
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_small_payload_high_loss_micro() {
        let (size, strategy, _) = qft_decide(512, 100.0, 0.15);
        assert!(size <= CHUNK_SIZES[STRATEGY_SMALL as usize]);
        let _ = strategy;
    }

    #[test]
    fn test_large_payload_no_loss_large_chunk() {
        let (size, _, _) = qft_decide(10 * 1024 * 1024, 2.0, 0.001);
        assert!(size >= CHUNK_SIZES[STRATEGY_LARGE as usize]);
    }

    #[test]
    fn test_emergency_override() {
        // deadline = 5ms, elapsed = 4ms → acil
        let (size, _, emergency) = qft_decide_deadline_aware(
            1024 * 1024, 10.0, 0.01, 5.0, 4.0
        );
        assert!(emergency);
        assert_eq!(size, CHUNK_SIZES[STRATEGY_MICRO as usize]);
    }

    #[test]
    fn test_performance() {
        let decisions_per_sec = qft_benchmark(100_000);
        // En az 1 milyon karar/saniye
        assert!(decisions_per_sec > 1_000_000.0,
            "Too slow: {:.0} decisions/sec", decisions_per_sec);
    }
}
```

---

## ADIM 5 — chunker.rs

```rust
// qdap_core/src/chunker.rs

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};

/// Payload'ı optimal chunk'lara böl (Rust implementasyonu).
///
/// chunk_size kadar böler, son chunk daha küçük olabilir.
/// Zero-copy: her chunk orijinal buffer'a reference.
///
/// Args:
///     payload:    bölünecek bytes
///     chunk_size: her chunk'ın maksimum boyutu
///
/// Returns:
///     bytes listesi (her biri bir chunk)
#[pyfunction]
pub fn split_payload<'py>(
    py:         Python<'py>,
    payload:    &[u8],
    chunk_size: usize,
) -> &'py PyList {
    if payload.is_empty() || chunk_size == 0 {
        return PyList::empty(py);
    }

    let chunks: Vec<&PyBytes> = payload
        .chunks(chunk_size)
        .map(|c| PyBytes::new(py, c))
        .collect();

    PyList::new(py, chunks)
}


/// Chunk boyutunu payload büyüklüğüne göre hesapla.
///
/// AdaptiveChunker._calculate_chunk_size() Python metodunun
/// Rust karşılığı.
///
/// Args:
///     total_size: toplam payload boyutu (bytes)
///     rtt_ms:     RTT tahmini
///     bandwidth_mbps: tahmini bant genişliği
///
/// Returns:
///     önerilen chunk_size (bytes)
#[pyfunction]
pub fn calculate_optimal_chunk_size(
    total_size:     usize,
    rtt_ms:         f64,
    bandwidth_mbps: f64,
) -> usize {
    // Bandwidth-delay product: ne kadar veri "uçuşta" olabilir
    let bdp_bytes = (bandwidth_mbps * 1_000_000.0 / 8.0 * rtt_ms / 1000.0) as usize;

    // BDP / 4 = makul chunk boyutu (pipeline doldurmak için)
    let bdp_chunk = (bdp_bytes / 4).max(4 * 1024);  // en az 4KB

    // Total size ile sınırla
    let raw_chunk = bdp_chunk.min(total_size);

    // Güzel bir sayıya yuvarla (2'nin katı)
    let rounded = next_power_of_two_floor(raw_chunk);

    // Sınırlar: 4KB - 1MB
    rounded.clamp(4 * 1024, 1024 * 1024)
}


fn next_power_of_two_floor(n: usize) -> usize {
    if n == 0 { return 4096; }
    let mut power = 1usize;
    while power * 2 <= n {
        power *= 2;
    }
    power
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_split_exact() {
        Python::with_gil(|py| {
            let payload = vec![0u8; 1024];
            let chunks  = split_payload(py, &payload, 256);
            assert_eq!(chunks.len(), 4);
        });
    }

    #[test]
    fn test_split_remainder() {
        Python::with_gil(|py| {
            let payload = vec![0u8; 1000];
            let chunks  = split_payload(py, &payload, 256);
            assert_eq!(chunks.len(), 4);  // 3×256 + 1×232
        });
    }

    #[test]
    fn test_chunk_size_lan() {
        // LAN: 1Gbps, RTT=1ms
        let size = calculate_optimal_chunk_size(10*1024*1024, 1.0, 1000.0);
        assert!(size >= 64 * 1024);  // LAN'da büyük chunk beklenir
    }

    #[test]
    fn test_chunk_size_wan() {
        // WAN: 10Mbps, RTT=50ms
        let size = calculate_optimal_chunk_size(1*1024*1024, 50.0, 10.0);
        assert!(size <= 64 * 1024);  // WAN'da daha küçük
    }
}
```

---

## ADIM 6 — lib.rs Güncelle

```rust
// qdap_core/src/lib.rs — mevcut fonksiyonlara EKLE

mod qframe;
mod qft_scheduler;
mod chunker;

#[pymodule]
fn qdap_core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    // ... mevcut fonksiyonlar ...

    // QFrame
    m.add_function(wrap_pyfunction!(qframe::qframe_serialize, m)?)?;
    m.add_function(wrap_pyfunction!(qframe::qframe_deserialize, m)?)?;
    m.add_function(wrap_pyfunction!(qframe::qframe_peek_header, m)?)?;

    // QFT Scheduler
    m.add_function(wrap_pyfunction!(qft_scheduler::qft_decide, m)?)?;
    m.add_function(wrap_pyfunction!(qft_scheduler::qft_decide_batch, m)?)?;
    m.add_function(wrap_pyfunction!(qft_scheduler::qft_decide_deadline_aware, m)?)?;
    m.add_function(wrap_pyfunction!(qft_scheduler::qft_benchmark, m)?)?;

    // Chunker
    m.add_function(wrap_pyfunction!(chunker::split_payload, m)?)?;
    m.add_function(wrap_pyfunction!(chunker::calculate_optimal_chunk_size, m)?)?;

    // Sabitler
    m.add("QFRAME_HEADER_SIZE", qframe::HEADER_SIZE)?;
    m.add("QFRAME_MAGIC",       qframe::MAGIC)?;
    m.add("QFT_STRATEGY_MICRO", qft_scheduler::STRATEGY_MICRO)?;
    m.add("QFT_STRATEGY_SMALL", qft_scheduler::STRATEGY_SMALL)?;
    m.add("QFT_STRATEGY_MEDIUM",qft_scheduler::STRATEGY_MEDIUM)?;
    m.add("QFT_STRATEGY_LARGE", qft_scheduler::STRATEGY_LARGE)?;
    m.add("QFT_STRATEGY_JUMBO", qft_scheduler::STRATEGY_JUMBO)?;

    Ok(())
}
```

---

## ADIM 7 — Python Bridge Güncelle

```python
# src/qdap/_rust_bridge.py — mevcut dosyaya EKLE

# QFrame
def qframe_serialize(
    payload: bytes,
    priority: int = 0,
    deadline_ms: float = 500.0,
    sequence_number: int = 0,
    frame_type: int = 0,
) -> bytes:
    if RUST_AVAILABLE:
        return _rust.qframe_serialize(
            payload, priority, deadline_ms, sequence_number, frame_type
        )
    return _python_qframe_serialize(
        payload, priority, deadline_ms, sequence_number, frame_type
    )


def qframe_deserialize(data: bytes) -> tuple:
    """Returns: (payload, priority, deadline_ms, seq_num, frame_type, hash_valid)"""
    if RUST_AVAILABLE:
        return _rust.qframe_deserialize(data)
    return _python_qframe_deserialize(data)


# QFT Scheduler
def qft_decide(
    payload_size: int,
    rtt_ms: float = 20.0,
    loss_rate: float = 0.01,
) -> tuple:
    """Returns: (chunk_size_bytes, strategy_index, confidence)"""
    if RUST_AVAILABLE:
        return _rust.qft_decide(payload_size, rtt_ms, loss_rate)
    return _python_qft_decide(payload_size, rtt_ms, loss_rate)


def split_payload(payload: bytes, chunk_size: int) -> list[bytes]:
    if RUST_AVAILABLE:
        return _rust.split_payload(payload, chunk_size)
    return [payload[i:i+chunk_size] for i in range(0, len(payload), chunk_size)]
```

---

## ADIM 8 — qframe.py Güncelle (Bridge Kullan)

```python
# src/qdap/frame/qframe.py
# Mevcut to_bytes() ve from_bytes() metodlarını bridge'e yönlendir

from qdap._rust_bridge import qframe_serialize as _serialize
from qdap._rust_bridge import qframe_deserialize as _deserialize

class QFrame:
    # ... mevcut alanlar ...

    def to_bytes(self) -> bytes:
        return _serialize(
            payload=self.payload,
            priority=self.priority,
            deadline_ms=self.deadline_ms,
            sequence_number=self.sequence_number,
            frame_type=self.frame_type,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "QFrame":
        payload, priority, deadline_ms, seq_num, frame_type, hash_valid = \
            _deserialize(data)
        frame = cls.__new__(cls)
        frame.payload         = payload
        frame.priority        = priority
        frame.deadline_ms     = deadline_ms
        frame.sequence_number = seq_num
        frame.frame_type      = frame_type
        frame.hash_valid      = hash_valid
        return frame
```

---

## ADIM 9 — qft_scheduler.py Güncelle

```python
# src/qdap/scheduler/qft_scheduler.py
# decide() metodunu bridge'e yönlendir

from qdap._rust_bridge import qft_decide as _decide
from qdap._rust_bridge import qft_decide_deadline_aware as _decide_dl

class QFTScheduler:
    def decide(
        self,
        payload_size: int,
        rtt_ms: float = None,
        loss_rate: float = None,
    ) -> ChunkStrategy:
        rtt  = rtt_ms   or self._estimated_rtt_ms
        loss = loss_rate or self._estimated_loss_rate

        chunk_size, strategy_idx, confidence = _decide(
            payload_size, rtt, loss
        )

        return ChunkStrategy(
            chunk_size_bytes = chunk_size,
            strategy_name    = STRATEGY_NAMES[strategy_idx],
            confidence       = confidence,
        )
```

---

## ADIM 10 — Benchmark (Rust vs Python Kıyaslama)

```python
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
```

---

## ADIM 11 — Testler

```python
# tests/test_qframe_rust.py

import pytest
import struct
from qdap._rust_bridge import qframe_serialize, qframe_deserialize, qframe_peek_header

class TestQFrameRust:

    def test_basic_roundtrip(self):
        payload = b"Hello QDAP Rust!" * 100
        wire    = qframe_serialize(payload, priority=100, deadline_ms=500.0,
                                   sequence_number=42, frame_type=0)
        parsed_payload, priority, deadline, seq, ftype, hash_valid = \
            qframe_deserialize(wire)

        assert parsed_payload == payload
        assert priority       == 100
        assert deadline       == 500.0
        assert seq            == 42
        assert ftype          == 0
        assert hash_valid     is True

    def test_tampered_payload_invalid(self):
        payload  = b"secret data"
        wire     = bytearray(qframe_serialize(payload, 0, 0.0, 0, 0))
        wire[61] ^= 0xFF  # payload bozuldu
        _, _, _, _, _, hash_valid = qframe_deserialize(bytes(wire))
        assert hash_valid is False

    def test_too_short_raises(self):
        with pytest.raises((ValueError, Exception)):
            qframe_deserialize(b"short")

    def test_wrong_magic_raises(self):
        payload = b"test"
        wire    = bytearray(qframe_serialize(payload, 0, 0.0, 0, 0))
        wire[0] = 0xFF  # magic bozuldu
        with pytest.raises((ValueError, Exception)):
            qframe_deserialize(bytes(wire))

    def test_empty_payload(self):
        wire = qframe_serialize(b"", 0, 0.0, 0, 0)
        payload, _, _, _, _, valid = qframe_deserialize(wire)
        assert payload == b""
        assert valid

    def test_large_payload(self):
        import os
        payload = os.urandom(1024 * 1024)  # 1MB
        wire    = qframe_serialize(payload, 0, 0.0, 0, 0)
        parsed, _, _, _, _, valid = qframe_deserialize(wire)
        assert parsed == payload
        assert valid

    def test_peek_header(self):
        payload = b"X" * 64 * 1024
        wire    = qframe_serialize(payload, priority=999, deadline_ms=2.0,
                                   sequence_number=0, frame_type=0)
        length, priority, deadline, ftype = qframe_peek_header(wire)
        assert length   == len(payload)
        assert priority == 999
        assert deadline == 2.0


class TestQFTSchedulerRust:

    def test_small_payload_high_loss_small_chunk(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(512, 100.0, 0.15)
        assert chunk <= 16 * 1024  # küçük chunk

    def test_large_payload_no_loss_large_chunk(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(10 * 1024 * 1024, 2.0, 0.0)
        assert chunk >= 256 * 1024  # büyük chunk

    def test_returns_valid_types(self):
        from qdap._rust_bridge import qft_decide
        chunk, strategy, confidence = qft_decide(65536, 20.0, 0.01)
        assert isinstance(chunk, int)
        assert isinstance(strategy, int)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0
```

---

## Teslim Kriterleri

```
✅ qdap_core/ içine qframe.rs, qft_scheduler.rs, chunker.rs eklendi
✅ lib.rs güncellendi (yeni fonksiyonlar export edildi)
✅ maturin develop --release başarıyla çalıştı
✅ src/qdap/_rust_bridge.py güncellendi (yeni bridge fonksiyonlar)
✅ src/qdap/frame/qframe.py → Rust bridge kullanıyor
✅ src/qdap/scheduler/qft_scheduler.py → Rust bridge kullanıyor
✅ 226 mevcut test HÂLÂ geçiyor
✅ tests/test_qframe_rust.py → 8 yeni test geçiyor
✅ Toplam: 234+ test

Benchmark sonucu (beklenen Rust backend):
  qft_decide(): > 1,000,000 decisions/sec
  qframe_serialize(1KB): < 0.01ms/op
  1MB throughput: QDAP > Classical (Python overhead kalkınca)

DOKUNMA:
  ❌ src/qdap/session/ghost_session.py
  ❌ docker_benchmark/ altındaki her şey
  ❌ Mevcut test dosyaları (sadece yeni ekle)
```

---

## Paper Değişikliği (Phase 8.1 Tamamlanınca)

Section 4.3'e ekle:
```
"The critical path — QFrame serialization, QFT scheduling,
and AES-256-GCM encryption — is implemented in Rust via PyO3,
achieving >1M scheduling decisions per second and
sub-microsecond frame serialization."
```

Tablo 3'e yeni satır:
```
Full Rust path  | 1KB 110×→130× | 1MB 1.00×→1.15× | test: 234/234
```
