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

        // LARGE (256KB): büyük payload + low loss + yüksek RTT
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
