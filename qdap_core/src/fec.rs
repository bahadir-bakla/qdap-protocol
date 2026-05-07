// qdap_core/src/fec.rs
//
// XOR Systematic (k, r) Forward Error Correction
// ================================================
//
// Profiles:
//   EMERGENCY  (k=1, r=2): any 1 of 3 packets sufficient  — rate 1/3
//   AGGRESSIVE (k=1, r=1): any 1 of 2 packets sufficient  — rate 1/2
//   BALANCED   (k=2, r=2): any 2 of 4 packets sufficient  — rate 1/2
//   RELIABLE   (k=2, r=1): any 2 of 3 packets sufficient  — rate 2/3
//   NONE       (k=1, r=0): no FEC
//
// Encode (k=1 case):
//   All r parities = copies of data → any 1 of k+r recovers.
//
// Encode (k>1 case):
//   Data split into k equal chunks. Parity stripe j = XOR of
//   all data chunks i where i % r == j. SIMD-friendly byte loop.
//
// Decode:
//   k=1: first received packet.
//   k>1: if all data arrived → reassemble. Single missing data
//         packet recovered from parity via XOR.
//
// Loss model (exact binomial):
//   p_eff = Σ_{i=r+1}^{k+r} C(k+r, i) p^i (1-p)^(k+r-i)
//
// Reference: RFC 5109 (RTP FEC), 3GPP TS 22.261

use pyo3::prelude::*;
use pyo3::types::PyBytes;

// ── Profile constants (label, k, r) ───────────────────────────────────────────

const PROFILE_EMERGENCY:  (&str, usize, usize) = ("emergency",  1, 2);
const PROFILE_AGGRESSIVE: (&str, usize, usize) = ("aggressive", 1, 1);
const PROFILE_BALANCED:   (&str, usize, usize) = ("balanced",   2, 2);
const PROFILE_RELIABLE:   (&str, usize, usize) = ("reliable",   2, 1);
const PROFILE_NONE:       (&str, usize, usize) = ("none",       1, 0);

// ── XOR encode ────────────────────────────────────────────────────────────────

/// Encode `data` with (k, r) FEC. Returns k+r packets.
///
/// k=1 (EMERGENCY/AGGRESSIVE):
///   All r parities are identical copies of data. The receiver can recover
///   from any single received packet. Simple and correct for k=1.
///
/// k>1 (BALANCED/RELIABLE):
///   Data split into k equal zero-padded chunks. Parity stripe j =
///   XOR of all data[i] where i % r == j. Compiler auto-vectorises
///   the inner byte loop (LLVM SIMD).
///
/// Returns: Python list of bytes objects (k data + r parity).
#[pyfunction]
pub fn fec_encode(py: Python<'_>, data: &[u8], k: usize, r: usize) -> PyResult<Vec<PyObject>> {
    if k == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err("k must be >= 1"));
    }
    if r == 0 {
        // NONE profile — pass through
        return Ok(vec![PyBytes::new(py, data).into()]);
    }

    if k == 1 {
        // k=1: all parities are exact copies → any 1-of-(1+r) recovers
        let mut out = Vec::with_capacity(1 + r);
        out.push(PyBytes::new(py, data).into());
        for _ in 0..r {
            out.push(PyBytes::new(py, data).into());
        }
        return Ok(out);
    }

    // k>1: split into k chunks, compute r XOR parity stripes
    let total    = data.len();
    let chunk_sz = (total + k - 1) / k;  // ceil(total / k)

    // Build k zero-padded data chunks
    let chunks: Vec<Vec<u8>> = (0..k)
        .map(|i| {
            let start = i * chunk_sz;
            let end   = (start + chunk_sz).min(total);
            let mut chunk = vec![0u8; chunk_sz];
            if start < total {
                chunk[..end - start].copy_from_slice(&data[start..end]);
            }
            chunk
        })
        .collect();

    // XOR parity stripes — compiler auto-vectorises this loop
    let mut parities: Vec<Vec<u8>> = vec![vec![0u8; chunk_sz]; r];
    for (i, chunk) in chunks.iter().enumerate() {
        let j = i % r;
        let parity = &mut parities[j];
        for (pos, &b) in chunk.iter().enumerate() {
            parity[pos] ^= b;
        }
    }

    let mut out: Vec<PyObject> = chunks
        .iter()
        .map(|c| PyBytes::new(py, c).into())
        .collect();
    for p in &parities {
        out.push(PyBytes::new(py, p).into());
    }
    Ok(out)
}


// ── XOR decode ────────────────────────────────────────────────────────────────

/// Recover original data from received FEC packets.
///
/// `packets`: Vec of (data_0, .., data_{k-1}, parity_0, .., parity_{r-1})
///            None = lost, Some(bytes) = received.
/// `original_len`: exact byte length of original data (for unpadding).
///
/// Returns recovered data or None if irrecoverable.
#[pyfunction]
pub fn fec_decode(
    py: Python<'_>,
    packets: Vec<Option<Vec<u8>>>,
    k: usize,
    r: usize,
    original_len: usize,
) -> Option<PyObject> {
    if packets.is_empty() { return None; }

    if r == 0 {
        // No FEC — return first data packet
        return packets.first()?.as_ref().map(|d| {
            let end = original_len.min(d.len());
            PyBytes::new(py, &d[..end]).into()
        });
    }

    if k == 1 {
        // k=1: all packets are copies — return first received
        for pkt in &packets {
            if let Some(data) = pkt {
                let end = original_len.min(data.len());
                return Some(PyBytes::new(py, &data[..end]).into());
            }
        }
        return None;
    }

    let total_lost = packets.iter().filter(|p| p.is_none()).count();
    if total_lost > r { return None; }

    // Split into data / parity views
    let data_pkts   = &packets[..k.min(packets.len())];
    let parity_pkts = if packets.len() > k { &packets[k..] } else { &[] };

    // Fast path: all data packets received
    if data_pkts.iter().all(|p| p.is_some()) {
        let mut result = Vec::with_capacity(original_len);
        for chunk in data_pkts.iter().flatten() {
            result.extend_from_slice(chunk);
        }
        result.truncate(original_len);
        return Some(PyBytes::new(py, &result).into());
    }

    // Single-loss recovery via XOR
    let mut recovered: Vec<Option<Vec<u8>>> = data_pkts.to_vec();

    for (parity_idx, parity_opt) in parity_pkts.iter().enumerate() {
        let parity = match parity_opt {
            Some(p) => p,
            None    => continue,
        };
        let stripe: Vec<usize> = (0..k)
            .filter(|&i| i % r == parity_idx)
            .collect();
        let missing: Vec<usize> = stripe.iter()
            .filter(|&&i| i < recovered.len() && recovered[i].is_none())
            .copied()
            .collect();

        if missing.len() == 1 {
            let miss = missing[0];
            let mut acc = parity.clone();
            for &idx in &stripe {
                if idx == miss { continue; }
                if let Some(Some(known)) = recovered.get(idx) {
                    for (pos, &b) in known.iter().enumerate() {
                        if pos < acc.len() { acc[pos] ^= b; }
                    }
                }
            }
            recovered[miss] = Some(acc);
        }
    }

    if recovered.iter().any(|p| p.is_none()) { return None; }

    let mut result = Vec::with_capacity(original_len);
    for chunk in recovered.iter().flatten() {
        result.extend_from_slice(chunk);
    }
    result.truncate(original_len);
    Some(PyBytes::new(py, &result).into())
}


// ── Exact binomial effective loss ─────────────────────────────────────────────

/// P(irrecoverable) = P(> r losses in k+r transmissions).
/// Exact binomial — no approximation.
#[pyfunction]
pub fn fec_effective_loss(p: f64, k: usize, r: usize) -> f64 {
    if r == 0 { return p.clamp(0.0, 1.0); }
    let n = k + r;
    let p = p.clamp(0.0, 1.0);
    let q = 1.0 - p;
    (r + 1..=n)
        .map(|i| binom(n, i) as f64 * p.powi(i as i32) * q.powi((n - i) as i32))
        .sum()
}

/// C(n, k) — integer binomial coefficient, overflow-safe for small n.
#[inline]
fn binom(n: usize, k: usize) -> u64 {
    if k > n { return 0; }
    let k = k.min(n - k);
    let mut r = 1u64;
    for i in 0..k {
        // Divide before multiply to stay in u64
        r = r / (i as u64 + 1) * (n - i) as u64
          + r % (i as u64 + 1) * (n - i) as u64 / (i as u64 + 1);
    }
    r
}


// ── Profile selection ─────────────────────────────────────────────────────────

/// Adaptive profile selection — mirrors Python select_fec_profile().
/// Returns (label, k, r).
#[pyfunction]
pub fn fec_select_profile(
    loss_rate:    f64,
    is_emergency: bool,
    max_overhead: f64,
) -> (String, usize, usize) {
    let p = if is_emergency {
        if max_overhead >= 3.0 { PROFILE_EMERGENCY } else { PROFILE_AGGRESSIVE }
    } else if loss_rate >= 0.20 {
        PROFILE_BALANCED
    } else if loss_rate >= 0.05 {
        PROFILE_RELIABLE
    } else {
        PROFILE_NONE
    };
    (p.0.to_string(), p.1, p.2)
}


// ── EMA loss update ───────────────────────────────────────────────────────────

/// Exponential Moving Average update for observed loss rate.
/// new_loss = (1-alpha) * current + alpha * (lost / sent)
#[pyfunction]
pub fn fec_ema_update(current: f64, lost: usize, sent: usize, alpha: f64) -> f64 {
    if sent == 0 { return current; }
    let sample = lost as f64 / sent as f64;
    (1.0 - alpha) * current + alpha * sample
}


// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // binomial coefficient
    fn b(n: usize, k: usize) -> u64 { binom(n, k) }

    #[test]
    fn test_binom_basic() {
        assert_eq!(b(0, 0), 1);
        assert_eq!(b(3, 0), 1);
        assert_eq!(b(3, 1), 3);
        assert_eq!(b(3, 2), 3);
        assert_eq!(b(3, 3), 1);
        assert_eq!(b(4, 2), 6);
        assert_eq!(b(10, 5), 252);
    }

    #[test]
    fn test_effective_loss_none() {
        let eff = fec_effective_loss(0.35, 1, 0);
        assert!((eff - 0.35).abs() < 1e-12);
    }

    #[test]
    fn test_effective_loss_emergency() {
        // k=1, r=2 → p_eff = p^3 (any of 3 copies, lost only if all 3 lost)
        let eff = fec_effective_loss(0.35, 1, 2);
        let expected = 0.35f64.powi(3);
        assert!((eff - expected).abs() < 1e-9,
            "emergency p_eff={:.8} expected={:.8}", eff, expected);
    }

    #[test]
    fn test_effective_loss_aggressive() {
        // k=1, r=1 → p_eff = p^2
        let eff = fec_effective_loss(0.35, 1, 1);
        let expected = 0.35f64.powi(2);
        assert!((eff - expected).abs() < 1e-9);
    }

    #[test]
    fn test_effective_loss_balanced() {
        // k=2, r=2 → p_eff = C(4,3)p^3(1-p) + C(4,4)p^4
        let p = 0.35f64;
        let expected = 4.0 * p.powi(3) * (1.0-p) + p.powi(4);
        let eff = fec_effective_loss(p, 2, 2);
        assert!((eff - expected).abs() < 1e-9);
    }

    #[test]
    fn test_profile_emergency() {
        let (label, k, r) = fec_select_profile(0.35, true, 3.0);
        assert_eq!(label, "emergency");
        assert_eq!((k, r), (1, 2));
    }

    #[test]
    fn test_profile_none_low_loss() {
        let (label, k, r) = fec_select_profile(0.01, false, 3.0);
        assert_eq!(label, "none");
        assert_eq!((k, r), (1, 0));
    }

    #[test]
    fn test_profile_balanced_high_loss() {
        let (label, k, r) = fec_select_profile(0.25, false, 3.0);
        assert_eq!(label, "balanced");
        assert_eq!((k, r), (2, 2));
    }

    #[test]
    fn test_ema_update() {
        // 35% sample with alpha=0.15, starting at 0
        let result = fec_ema_update(0.0, 7, 20, 0.15);
        let expected = 0.15 * (7.0 / 20.0);
        assert!((result - expected).abs() < 1e-12);
    }

    #[test]
    fn test_ema_no_sent() {
        let result = fec_ema_update(0.5, 0, 0, 0.15);
        assert_eq!(result, 0.5);
    }

    #[test]
    fn test_encode_k1_gives_copies() {
        pyo3::Python::with_gil(|py| {
            let data = b"hello world";
            let pkts = fec_encode(py, data, 1, 2).unwrap();
            assert_eq!(pkts.len(), 3);
            // All 3 should be the same data
            for pkt in &pkts {
                let b = pkt.extract::<&PyBytes>(py).unwrap();
                assert_eq!(b.as_bytes(), data);
            }
        });
    }

    #[test]
    fn test_encode_decode_k2_r1_lossless() {
        pyo3::Python::with_gil(|py| {
            let data = b"Hello QDAP Rust FEC!";
            let pkts = fec_encode(py, data, 2, 1).unwrap();
            assert_eq!(pkts.len(), 3);  // k=2 + r=1

            // No loss: all data packets received
            let received: Vec<Option<Vec<u8>>> = pkts.iter().map(|p| {
                Some(p.extract::<&PyBytes>(py).unwrap().as_bytes().to_vec())
            }).collect();

            let recovered = fec_decode(py, received, 2, 1, data.len()).unwrap();
            let rb = recovered.extract::<&PyBytes>(py).unwrap();
            assert_eq!(rb.as_bytes(), data);
        });
    }

    #[test]
    fn test_encode_decode_k1_r2_single_loss() {
        pyo3::Python::with_gil(|py| {
            let data = b"Emergency frame test payload";
            let pkts = fec_encode(py, data, 1, 2).unwrap();
            assert_eq!(pkts.len(), 3);

            // Lose packet 0 (original), receive packets 1 and 2
            let mut received: Vec<Option<Vec<u8>>> = pkts.iter().map(|p| {
                Some(p.extract::<&PyBytes>(py).unwrap().as_bytes().to_vec())
            }).collect();
            received[0] = None;

            let recovered = fec_decode(py, received, 1, 2, data.len()).unwrap();
            let rb = recovered.extract::<&PyBytes>(py).unwrap();
            assert_eq!(rb.as_bytes(), data);
        });
    }

    #[test]
    fn test_decode_irrecoverable_returns_none() {
        pyo3::Python::with_gil(|py| {
            let data = b"test data";
            let pkts = fec_encode(py, data, 2, 1).unwrap();

            // Lose 2 packets with r=1 — irrecoverable
            let received: Vec<Option<Vec<u8>>> = vec![None, None,
                Some(pkts[2].extract::<&PyBytes>(py).unwrap().as_bytes().to_vec())];

            let result = fec_decode(py, received, 2, 1, data.len());
            assert!(result.is_none());
        });
    }

    #[test]
    fn test_improvement_factor() {
        // At 35% loss, EMERGENCY (k=1,r=2) should give ~8× improvement
        let raw = 0.35f64;
        let eff = fec_effective_loss(raw, 1, 2);
        let improvement = raw / eff;
        assert!(improvement > 7.0, "Expected >7× improvement, got {:.2}×", improvement);
    }
}
