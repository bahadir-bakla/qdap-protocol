// qdap_core/src/qft_scheduler.rs
//
// Log-linear (softmax) strateji seçimi.
//
// Önceki versiyon: doğrusal skor toplamı → argmax
// Yeni versiyon  : log-uzayında ağırlık güncellemesi (softmax)
//
// Neden daha iyi?
//   1. Ağırlıklar otomatik [0,1] içinde kalır — ayrı normalizasyon gerekmez.
//   2. Düşük ağırlıklı stratejiler sıfıra çökmez (log-space gradient).
//   3. Throughput hedefinde log(T) maksimize etmek, outlier RTT'lere karşı
//      robust — hocamın önerisinin tam karşılığı.
//   4. Teorik bağlantı: policy-gradient / multi-armed bandit literatürü.
//
// Matematiksel temel (bkz. math PDF §4):
//   θ_i(t+1) = θ_i(t) + lr · ∇ log π(s*|θ)
//   w_i(t)   = exp(θ_i(t)) / Σ_j exp(θ_j(t))   (softmax)
//
// Loss function (log-latency minimizasyonu):
//   L_log(C) = log(C/B + RTT + p_loss·T(C))
//   Bu, dL/dC = L'(C)/L(C) = 0 → L'(C) = 0 ile aynı C*'yi verir
//   ama curvature farkı sayesinde outlier RTT'lere daha az hassas.

use pyo3::prelude::*;
use std::sync::Mutex;

// ── Sabitler ─────────────────────────────────────────────────────────────────

pub const STRATEGY_MICRO:  u8 = 0;  // 4 KB  — yüksek loss / acil
pub const STRATEGY_SMALL:  u8 = 1;  // 16 KB — orta loss
pub const STRATEGY_MEDIUM: u8 = 2;  // 64 KB — normal WAN
pub const STRATEGY_LARGE:  u8 = 3;  // 256 KB — düşük loss
pub const STRATEGY_JUMBO:  u8 = 4;  // 1 MB  — LAN / veri merkezi

pub const CHUNK_SIZES: [usize; 5] = [
    4   * 1024,
    16  * 1024,
    64  * 1024,
    256 * 1024,
    1024 * 1024,
];

pub const STRATEGY_NAMES: [&str; 5] = [
    "MICRO", "SMALL", "MEDIUM", "LARGE", "JUMBO",
];

// Öğrenme hızı — log-uzayı güncellemesi için
const LR: f64 = 0.15;
// Sayısal kararlılık için küçük epsilon
const EPS: f64 = 1e-9;
// Pencere boyutu (observation window)
const WINDOW: usize = 1024;

// ── Thread-local ağırlıklar ───────────────────────────────────────────────────
// Log-uzayı parametreleri θ_i; w_i = softmax(θ)
// Başlangıç: θ = 0 → tüm stratejiler eşit olasılık (1/5)
use std::cell::RefCell;
thread_local! {
    static THETA: RefCell<[f64; 5]> = RefCell::new([0.0; 5]);
}

// ── Yardımcı: softmax ─────────────────────────────────────────────────────────
#[inline]
fn softmax(theta: &[f64; 5]) -> [f64; 5] {
    // Sayısal overflow'u önlemek için max çıkar
    let max = theta.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let mut out = [0.0f64; 5];
    let mut sum = 0.0;
    for i in 0..5 {
        out[i] = (theta[i] - max).exp();
        sum += out[i];
    }
    for i in 0..5 { out[i] /= sum + EPS; }
    out
}

// ── Yardımcı: kanal feature → ham skor ───────────────────────────────────────
// Bu ham skorlar θ güncellenmeden önce "prior" olarak kullanılır.
// Log ölçeğinde hesaplanır → outlier RTT'lere robust.
#[inline]
fn channel_log_scores(payload_size: usize, rtt_ms: f64, loss_rate: f64) -> [f64; 5] {
    // Log-normalise: outlier değerlere karşı robust
    let payload_norm = (payload_size as f64 + 1.0).ln() / (100.0_f64 * 1024.0 * 1024.0_f64).ln();
    let payload_norm = payload_norm.clamp(0.0, 1.0);

    // Log-RTT: 1ms → ~0, 500ms → ~1
    let rtt_norm = ((rtt_ms + 1.0).ln() / (501.0_f64).ln()).clamp(0.0, 1.0);

    // Log-loss: 0 → 0, 0.2 → 1
    let loss_norm = (loss_rate / 0.2).clamp(0.0, 1.0);

    // Log-latency hedefli skorlar:
    // Düşük L_log(C) = log(C/B + RTT + p*T) tercih edilir.
    // Her strateji için beklenen log-latency'ye ters orantılı skor.
    [
        // MICRO: küçük payload + yüksek loss + yüksek RTT → küçük C iyi
        ((1.0 - payload_norm) * 0.35 + loss_norm * 0.45 + rtt_norm * 0.20).ln_1p(),

        // SMALL: küçük-orta payload + orta loss
        ((1.0 - payload_norm).powi(2) * 0.40
            + (loss_norm * (1.0 - loss_norm)) * 0.40
            + 0.20).ln_1p(),

        // MEDIUM: orta payload, normal koşullar
        ((1.0 - (payload_norm - 0.5).abs() * 2.0).max(0.0) * 0.50
            + (1.0 - loss_norm) * 0.30
            + 0.20).ln_1p(),

        // LARGE: büyük payload + düşük loss + yüksek RTT
        (payload_norm * 0.40
            + (1.0 - loss_norm) * 0.40
            + rtt_norm * 0.20).ln_1p(),

        // JUMBO: çok büyük payload + sıfır loss + düşük RTT (LAN)
        (payload_norm.powi(2) * 0.50
            + (1.0 - loss_norm).powi(2) * 0.40
            + (1.0 - rtt_norm) * 0.10).ln_1p(),
    ]
}

// ── Ağırlık güncellemesi (log-linear policy gradient) ────────────────────────
// Seçilen strateji `best` için θ güncellenir:
//   θ_i += lr · (𝟙{i==best} - w_i)
// Bu, log π(s*|θ) gradyanının tam adımıdır.
fn update_theta(best: usize) {
    THETA.with(|t| {
        let mut theta = t.borrow_mut();
        let w = softmax(&*theta);
        for i in 0..5 {
            let indicator = if i == best { 1.0 } else { 0.0 };
            theta[i] += LR * (indicator - w[i]);
        }
    });
}

// ── Ana karar fonksiyonu ──────────────────────────────────────────────────────

/// QFT-ilhamlı log-linear strateji kararı.
///
/// Matematiksel temel:
///   combined_i = w_i(t) · exp(channel_score_i)
///   s* = argmax_i combined_i
///   θ güncelleme: θ_i += lr·(𝟙{s*=i} − w_i)
///
/// Args:
///     payload_size : toplam payload (bytes)
///     rtt_ms       : tahmin RTT (ms)
///     loss_rate    : paket kaybı [0.0, 1.0]
///
/// Returns:
///     (chunk_size_bytes, strategy_index, confidence)
#[pyfunction]
pub fn qft_decide(
    payload_size: usize,
    rtt_ms:       f64,
    loss_rate:    f64,
) -> (usize, u8, f64) {

    let ch_scores = channel_log_scores(payload_size, rtt_ms, loss_rate);

    let best_idx = THETA.with(|t| {
        let theta = t.borrow();
        let w = softmax(&*theta);

        // combined = log(w_i) + channel_score_i
        // → argmax eşdeğeri log-uzayında çarpımın
        let mut best = 0;
        let mut best_val = f64::NEG_INFINITY;
        for i in 0..5 {
            let val = (w[i] + EPS).ln() + ch_scores[i];
            if val > best_val {
                best_val = val;
                best = i;
            }
        }
        best
    });

    // Ağırlıkları güncelle
    update_theta(best_idx);

    // Confidence: top-1 ile top-2 arasındaki log-uzayı farkı
    let confidence = THETA.with(|t| {
        let theta = t.borrow();
        let w = softmax(&*theta);
        let mut sorted = w;
        sorted.sort_by(|a, b| b.partial_cmp(a).unwrap());
        ((sorted[0] - sorted[1]) / (sorted[0] + EPS)).clamp(0.0, 1.0)
    });

    (CHUNK_SIZES[best_idx], best_idx as u8, confidence)
}

// ── Batch karar ───────────────────────────────────────────────────────────────

#[pyfunction]
pub fn qft_decide_batch(
    payloads: Vec<(usize, f64, f64)>,
) -> Vec<(usize, u8, f64)> {
    payloads.iter()
        .map(|&(size, rtt, loss)| qft_decide(size, rtt, loss))
        .collect()
}

// ── Deadline-aware override ───────────────────────────────────────────────────

#[pyfunction]
pub fn qft_decide_deadline_aware(
    payload_size: usize,
    rtt_ms:       f64,
    loss_rate:    f64,
    deadline_ms:  f64,
    elapsed_ms:   f64,
) -> (usize, u8, bool) {
    let remaining_ms = deadline_ms - elapsed_ms;
    let is_emergency = remaining_ms < rtt_ms * 2.0 || remaining_ms < 5.0;

    if is_emergency {
        // Acil: MICRO — ağırlıkları da güncelle (emergency feedback)
        update_theta(STRATEGY_MICRO as usize);
        return (CHUNK_SIZES[STRATEGY_MICRO as usize], STRATEGY_MICRO, true);
    }

    let (chunk_size, strategy, _) = qft_decide(payload_size, rtt_ms, loss_rate);
    (chunk_size, strategy, false)
}

// ── Ağırlık sorgulama (Python bridge) ────────────────────────────────────────

/// Mevcut softmax ağırlıklarını döndür — debug / monitoring için
#[pyfunction]
pub fn qft_get_weights() -> [f64; 5] {
    THETA.with(|t| softmax(&*t.borrow()))
}

/// Ağırlıkları sıfırla (test / yeni bağlantı başlangıcı)
#[pyfunction]
pub fn qft_reset_weights() {
    THETA.with(|t| {
        *t.borrow_mut() = [0.0; 5];
    });
}

/// Dışarıdan θ vektörü yükle (session resume için)
#[pyfunction]
pub fn qft_load_theta(theta: Vec<f64>) -> bool {
    if theta.len() != 5 {
        return false;
    }
    THETA.with(|t| {
        let mut th = t.borrow_mut();
        for i in 0..5 {
            th[i] = theta[i];
        }
    });
    true
}

/// Mevcut θ vektörünü dışarı ver (session save için)
#[pyfunction]
pub fn qft_dump_theta() -> Vec<f64> {
    THETA.with(|t| t.borrow().to_vec())
}

// ── Benchmark ─────────────────────────────────────────────────────────────────

#[pyfunction]
pub fn qft_benchmark(n: usize) -> f64 {
    use std::time::Instant;
    qft_reset_weights();
    let t0 = Instant::now();
    let mut total = 0usize;
    for i in 0..n {
        let (chunk, _, _) = qft_decide(
            1024 * (1 + i % 1024),
            20.0 + (i % 100) as f64,
            0.01 * (i % 20) as f64,
        );
        total += chunk;
    }
    let elapsed = t0.elapsed().as_secs_f64();
    let _ = total;
    n as f64 / elapsed
}

// ── Testler ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn reset() { qft_reset_weights(); }

    #[test]
    fn test_small_payload_high_loss_prefers_micro_or_small() {
        reset();
        // Yüksek loss → küçük chunk beklenir
        for _ in 0..20 {
            let (size, _, _) = qft_decide(512, 100.0, 0.15);
            assert!(size <= CHUNK_SIZES[STRATEGY_SMALL as usize],
                "Expected MICRO/SMALL for high-loss small payload, got {}", size);
        }
    }

    #[test]
    fn test_large_payload_no_loss_large_chunk() {
        reset();
        // Birkaç iterasyon sonra LARGE/JUMBO'ya yakınsıyor mu?
        for _ in 0..30 {
            qft_decide(10 * 1024 * 1024, 2.0, 0.0001);
        }
        let (size, _, _) = qft_decide(10 * 1024 * 1024, 2.0, 0.0001);
        assert!(size >= CHUNK_SIZES[STRATEGY_LARGE as usize],
            "Expected LARGE/JUMBO for big clean payload, got {}", size);
    }

    #[test]
    fn test_softmax_weights_sum_to_one() {
        reset();
        qft_decide(1024, 20.0, 0.01);
        let w = qft_get_weights();
        let sum: f64 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-6, "Weights sum = {}", sum);
    }

    #[test]
    fn test_weights_non_negative() {
        reset();
        for i in 0..100 {
            qft_decide(1024 * (i + 1), 10.0 + i as f64, 0.001 * i as f64);
        }
        let w = qft_get_weights();
        for (i, &wi) in w.iter().enumerate() {
            assert!(wi >= 0.0, "Weight[{}] = {} < 0", i, wi);
        }
    }

    #[test]
    fn test_weights_adapt_to_channel() {
        reset();
        // Yüksek loss kanalı → MICRO ağırlığı artmalı
        for _ in 0..50 {
            qft_decide(512, 200.0, 0.18);
        }
        let w = qft_get_weights();
        assert!(w[STRATEGY_MICRO as usize] > w[STRATEGY_JUMBO as usize],
            "MICRO weight ({:.3}) should exceed JUMBO ({:.3}) on lossy channel",
            w[STRATEGY_MICRO as usize], w[STRATEGY_JUMBO as usize]);
    }

    #[test]
    fn test_emergency_override() {
        reset();
        let (size, _, emergency) =
            qft_decide_deadline_aware(1024 * 1024, 10.0, 0.01, 5.0, 4.0);
        assert!(emergency, "Should be emergency");
        assert_eq!(size, CHUNK_SIZES[STRATEGY_MICRO as usize]);
    }

    #[test]
    fn test_confidence_in_range() {
        reset();
        let (_, _, conf) = qft_decide(65536, 20.0, 0.01);
        assert!(conf >= 0.0 && conf <= 1.0, "Confidence out of range: {}", conf);
    }

    #[test]
    fn test_reset_clears_weights() {
        // Birkaç karar al, ağırlıklar değişir
        for _ in 0..20 { qft_decide(512, 200.0, 0.15); }
        qft_reset_weights();
        let w = qft_get_weights();
        // Sıfırlandıktan sonra uniform (1/5 = 0.2)
        for &wi in &w {
            assert!((wi - 0.2).abs() < 1e-6, "Expected 0.2 after reset, got {}", wi);
        }
    }

    #[test]
    fn test_performance() {
        let dps = qft_benchmark(100_000);
        assert!(dps > 1_000_000.0,
            "Too slow: {:.0} decisions/sec", dps);
    }
}