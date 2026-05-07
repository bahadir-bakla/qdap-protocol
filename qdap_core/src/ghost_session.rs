// qdap_core/src/ghost_session.rs
//
// GhostSession — Entanglement-Inspired Zero-ACK Protocol (Rust Core)
// ===================================================================
//
// Architecture:
//   This module provides the hot-path Rust implementation of GhostSession:
//
//   1. ghost_sign / ghost_verify
//      HMAC-SHA256 truncated to 8 bytes. Both sender and receiver compute
//      the same signature from (key, seq_num, payload[:32]) deterministically.
//      No network round-trip needed — implicit "collapse" like Bell measurement.
//
//   2. GhostWindow (#[pyclass])
//      Lock-free HashMap tracking in-flight packets with nanosecond timestamps.
//      - add():           record sent packet
//      - implicit_ack():  remove from window, record RTT
//      - detect_loss():   age-based heuristic (2.5× expected RTT → suspect lost)
//      - cleanup():       evict oldest quarter when window > max_size
//
//   Python GhostSession wraps GhostWindow via _rust_bridge, keeping the Markov
//   chain, HKDF key derivation, and QFrame building in Python (asyncio-compatible).
//
// HMAC formula (identical to Python):
//   msg = seq_num.to_bytes(4, 'big') ++ payload[:32]
//   sig = HMAC-SHA256(ghost_key, msg)[:8]
//
// Loss detection heuristic:
//   age_ns > threshold_mult × expected_rtt_ms × 1_000_000
//   Default threshold_mult = 2.5 (same as Python)

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;


// ── HMAC ghost signature ──────────────────────────────────────────────────────

/// Compute 8-byte truncated HMAC-SHA256 ghost signature.
///
/// Identical to Python:
///   hmac.new(ghost_key, seq_num.to_bytes(4,'big') + payload[:32], sha256).digest()[:8]
///
/// Both sender and receiver compute the same value without network exchange.
#[pyfunction]
pub fn ghost_sign<'py>(
    py:      Python<'py>,
    key:     &[u8],
    seq_num: u32,
    payload: &[u8],
) -> PyResult<&'py PyBytes> {
    let mut mac = HmacSha256::new_from_slice(key)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(
            format!("Invalid HMAC key length: {}", e)
        ))?;
    mac.update(&seq_num.to_be_bytes());
    let prefix_len = payload.len().min(32);
    mac.update(&payload[..prefix_len]);
    let tag = mac.finalize().into_bytes();
    Ok(PyBytes::new(py, &tag[..8]))
}

/// Verify an 8-byte ghost signature (constant-time via HMAC verify).
///
/// Returns True if signature matches, False otherwise.
/// Constant-time: safe against timing side-channel attacks.
#[pyfunction]
pub fn ghost_verify(key: &[u8], seq_num: u32, payload: &[u8], sig: &[u8]) -> bool {
    if sig.len() != 8 { return false; }
    let mut mac = match HmacSha256::new_from_slice(key) {
        Ok(m)  => m,
        Err(_) => return false,
    };
    mac.update(&seq_num.to_be_bytes());
    let prefix_len = payload.len().min(32);
    mac.update(&payload[..prefix_len]);
    let tag = mac.finalize().into_bytes();
    // Constant-time comparison of first 8 bytes
    tag[..8].iter().zip(sig.iter()).fold(0u8, |acc, (a, b)| acc | (a ^ b)) == 0
}


// ── GhostWindow ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct GhostEntry {
    sent_at_ns:   u64,
    payload_hash: [u8; 8],  // truncated hash for verification
}

/// Rust-backed in-flight packet window for GhostSession.
///
/// Exposed to Python as a class via #[pyclass]. Python GhostSession
/// delegates window management here; the rest (Markov chain, QFrame,
/// HKDF) remains in Python for asyncio compatibility.
#[pyclass]
pub struct GhostWindow {
    window:       HashMap<u64, GhostEntry>,
    max_size:     usize,
    total_acked:  u64,
    total_lost:   u64,
    rtt_samples:  Vec<f64>,
    rtt_max_samples: usize,
}

#[pymethods]
impl GhostWindow {
    /// Create a new GhostWindow.
    /// max_size: evict oldest quarter when window exceeds this (default 1024).
    #[new]
    #[pyo3(signature = (max_size = 1024, rtt_max_samples = 200))]
    pub fn new(max_size: usize, rtt_max_samples: usize) -> Self {
        GhostWindow {
            window: HashMap::with_capacity(64),
            max_size,
            total_acked: 0,
            total_lost: 0,
            rtt_samples: Vec::new(),
            rtt_max_samples,
        }
    }

    /// Record a sent packet in the window.
    ///
    /// seq_num:      sequence number (u64 for wrap safety)
    /// sent_at_ns:   monotonic timestamp in nanoseconds (time.monotonic_ns())
    /// payload_hash: first N bytes of payload hash (for optional verification)
    pub fn add(&mut self, seq_num: u64, sent_at_ns: u64, payload_hash: &[u8]) {
        let mut hash = [0u8; 8];
        let copy = payload_hash.len().min(8);
        hash[..copy].copy_from_slice(&payload_hash[..copy]);

        self.window.insert(seq_num, GhostEntry { sent_at_ns, payload_hash: hash });

        if self.window.len() > self.max_size {
            self.evict_oldest();
        }
    }

    /// Implicit ACK: packet confirmed received (e.g. via piggyback or side-channel).
    ///
    /// Removes packet from window, records RTT sample.
    /// Returns RTT in milliseconds, or -1.0 if seq_num not in window.
    pub fn implicit_ack(&mut self, seq_num: u64, now_ns: u64) -> f64 {
        match self.window.remove(&seq_num) {
            Some(entry) => {
                let rtt_ms = (now_ns.saturating_sub(entry.sent_at_ns)) as f64 / 1_000_000.0;
                self.total_acked += 1;
                // Rolling RTT buffer
                if self.rtt_samples.len() >= self.rtt_max_samples {
                    self.rtt_samples.remove(0);
                }
                self.rtt_samples.push(rtt_ms);
                rtt_ms
            }
            None => -1.0,
        }
    }

    /// Detect lost packets via age heuristic.
    ///
    /// Packets older than threshold_mult × expected_rtt_ms are considered lost.
    /// Default threshold_mult = 2.5 (same as Python detect_loss).
    ///
    /// Detected packets are removed from window and counted as lost.
    /// Returns list of lost seq_nums for retransmit scheduling.
    #[pyo3(signature = (now_ns, expected_rtt_ms, threshold_mult = 2.5))]
    pub fn detect_loss(&mut self, now_ns: u64, expected_rtt_ms: f64, threshold_mult: f64) -> Vec<u64> {
        let threshold_ns = (expected_rtt_ms * threshold_mult * 1_000_000.0) as u64;

        let lost: Vec<u64> = self.window.iter()
            .filter(|(_, entry)| {
                now_ns >= entry.sent_at_ns
                    && (now_ns - entry.sent_at_ns) > threshold_ns
            })
            .map(|(&seq, _)| seq)
            .collect();

        for &seq in &lost {
            self.window.remove(&seq);
            self.total_lost += 1;
        }

        lost
    }

    /// Remove a single seq_num from the window without ACK accounting.
    /// Returns True if it was present.
    pub fn remove(&mut self, seq_num: u64) -> bool {
        self.window.remove(&seq_num).is_some()
    }

    /// Check if a seq_num is currently in the window.
    pub fn contains(&self, seq_num: u64) -> bool {
        self.window.contains_key(&seq_num)
    }

    /// Clear window and reset all counters.
    pub fn reset(&mut self) {
        self.window.clear();
        self.total_acked = 0;
        self.total_lost = 0;
        self.rtt_samples.clear();
    }

    // ── Properties ────────────────────────────────────────────────────────────

    #[getter]
    pub fn pending_count(&self) -> usize {
        self.window.len()
    }

    #[getter]
    pub fn total_acked(&self) -> u64 {
        self.total_acked
    }

    #[getter]
    pub fn total_lost(&self) -> u64 {
        self.total_lost
    }

    /// Mean RTT over recent samples (0.0 if no samples yet).
    pub fn avg_rtt_ms(&self) -> f64 {
        if self.rtt_samples.is_empty() { return 0.0; }
        self.rtt_samples.iter().sum::<f64>() / self.rtt_samples.len() as f64
    }

    /// Recent RTT samples (up to rtt_max_samples).
    pub fn rtt_samples(&self) -> Vec<f64> {
        self.rtt_samples.clone()
    }

    /// Sequence numbers currently in the window.
    pub fn pending_seqs(&self) -> Vec<u64> {
        self.window.keys().copied().collect()
    }

    // ── Internal ──────────────────────────────────────────────────────────────

    fn evict_oldest(&mut self) {
        let evict_n = self.window.len() / 4;
        if evict_n == 0 { return; }

        let mut by_age: Vec<(u64, u64)> = self.window.iter()
            .map(|(&seq, e)| (seq, e.sent_at_ns))
            .collect();
        by_age.sort_unstable_by_key(|&(_, ts)| ts);

        for (seq, _) in by_age.into_iter().take(evict_n) {
            self.window.remove(&seq);
            self.total_lost += 1;
        }
    }
}


// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &[u8] = b"ghost-key-32-bytes-padded-here!";

    #[test]
    fn test_sign_is_8_bytes() {
        pyo3::Python::with_gil(|py| {
            let sig = ghost_sign(py, KEY, 42, b"payload").unwrap();
            assert_eq!(sig.as_bytes().len(), 8);
        });
    }

    #[test]
    fn test_sign_verify_match() {
        pyo3::Python::with_gil(|py| {
            let payload = b"emergency SOS message";
            let sig = ghost_sign(py, KEY, 100, payload).unwrap();
            assert!(ghost_verify(KEY, 100, payload, sig.as_bytes()));
        });
    }

    #[test]
    fn test_verify_wrong_seq() {
        pyo3::Python::with_gil(|py| {
            let payload = b"test";
            let sig = ghost_sign(py, KEY, 1, payload).unwrap();
            assert!(!ghost_verify(KEY, 2, payload, sig.as_bytes()));
        });
    }

    #[test]
    fn test_verify_tampered_payload() {
        pyo3::Python::with_gil(|py| {
            let payload = b"original";
            let sig = ghost_sign(py, KEY, 1, payload).unwrap();
            assert!(!ghost_verify(KEY, 1, b"tampered", sig.as_bytes()));
        });
    }

    #[test]
    fn test_verify_wrong_sig_length() {
        assert!(!ghost_verify(KEY, 1, b"data", &[0u8; 4]));
        assert!(!ghost_verify(KEY, 1, b"data", &[0u8; 16]));
    }

    #[test]
    fn test_deterministic_sign() {
        pyo3::Python::with_gil(|py| {
            let sig1 = ghost_sign(py, KEY, 99, b"hello").unwrap().as_bytes().to_vec();
            let sig2 = ghost_sign(py, KEY, 99, b"hello").unwrap().as_bytes().to_vec();
            assert_eq!(sig1, sig2);
        });
    }

    #[test]
    fn test_window_add_and_ack() {
        let mut w = GhostWindow::new(1024, 200);
        w.add(1, 1_000_000_000, b"hash");
        assert_eq!(w.pending_count(), 1);
        assert!(w.contains(1));

        // 10ms later
        let rtt = w.implicit_ack(1, 1_010_000_000);
        assert!((rtt - 10.0).abs() < 0.01, "RTT={}", rtt);
        assert_eq!(w.pending_count(), 0);
        assert_eq!(w.total_acked(), 1);
    }

    #[test]
    fn test_window_detect_loss() {
        let mut w = GhostWindow::new(1024, 200);
        // Packet sent 200ms ago, expected_rtt=20ms, threshold=2.5 → 50ms → lost
        let base_ns: u64 = 0;
        let now_ns: u64  = 200_000_000;  // 200ms later
        w.add(42, base_ns, b"");

        let lost = w.detect_loss(now_ns, 20.0, 2.5);
        assert_eq!(lost, vec![42]);
        assert_eq!(w.pending_count(), 0);
        assert_eq!(w.total_lost(), 1);
    }

    #[test]
    fn test_window_no_false_positive() {
        let mut w = GhostWindow::new(1024, 200);
        // Packet sent 30ms ago, expected_rtt=20ms, threshold=2.5 → 50ms → NOT lost
        let base_ns: u64 = 0;
        let now_ns: u64  = 30_000_000;  // 30ms later
        w.add(1, base_ns, b"");

        let lost = w.detect_loss(now_ns, 20.0, 2.5);
        assert!(lost.is_empty(), "Should not be lost yet");
        assert_eq!(w.pending_count(), 1);
    }

    #[test]
    fn test_window_eviction() {
        let mut w = GhostWindow::new(4, 200);  // small window
        for i in 0..8u64 {
            w.add(i, i * 1000, b"");
        }
        // Window should be <= max_size after eviction
        assert!(w.pending_count() <= 4);
    }

    #[test]
    fn test_avg_rtt() {
        let mut w = GhostWindow::new(1024, 200);
        // Two packets: 10ms and 20ms RTT
        w.add(1, 0, b"");
        w.add(2, 0, b"");
        w.implicit_ack(1, 10_000_000);   // 10ms
        w.implicit_ack(2, 20_000_000);   // 20ms
        let avg = w.avg_rtt_ms();
        assert!((avg - 15.0).abs() < 0.01, "avg RTT={}", avg);
    }

    #[test]
    fn test_reset_clears_all() {
        let mut w = GhostWindow::new(1024, 200);
        w.add(1, 0, b"");
        w.implicit_ack(1, 100_000_000);
        w.reset();
        assert_eq!(w.pending_count(), 0);
        assert_eq!(w.total_acked(), 0);
        assert_eq!(w.total_lost(), 0);
        assert_eq!(w.avg_rtt_ms(), 0.0);
    }
}
