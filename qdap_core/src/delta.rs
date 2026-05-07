// qdap_core/src/delta.rs
//
// Binary Delta Protocol — Protocol Framing Layer
// ================================================
//
// Wire format:
//   FULL:  [0x00][serialized_payload_bytes]
//   DELTA: [0x01][bitmask_u16_be][serialized_changed_bytes]
//   NO-OP: [0x01][0x00][0x00]   (bitmask=0, nothing changed)
//
// Responsibility split:
//   Rust:   frame wrapping/unwrapping, bitmask computation, header parsing.
//   Python: dict → bytes serialization (msgpack or json) — stays in Python
//           since it operates on Python objects.
//
// This keeps Rust zero-copy for the hot path (byte array operations)
// while Python handles the serialization format once per frame.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

pub const FRAME_FULL:  u8 = 0x00;
pub const FRAME_DELTA: u8 = 0x01;


// ── Frame wrapping ────────────────────────────────────────────────────────────

/// Wrap pre-serialized payload as FULL frame: [0x00][payload]
#[pyfunction]
pub fn delta_wrap_full<'py>(py: Python<'py>, payload: &[u8]) -> &'py PyBytes {
    let mut out = Vec::with_capacity(1 + payload.len());
    out.push(FRAME_FULL);
    out.extend_from_slice(payload);
    PyBytes::new(py, &out)
}

/// Wrap pre-serialized delta payload as DELTA frame: [0x01][bitmask_be16][payload]
/// If payload is empty and bitmask == 0: returns no-op frame [0x01][0x00][0x00].
#[pyfunction]
pub fn delta_wrap_delta<'py>(py: Python<'py>, bitmask: u16, payload: &[u8]) -> &'py PyBytes {
    let mut out = Vec::with_capacity(3 + payload.len());
    out.push(FRAME_DELTA);
    out.push((bitmask >> 8) as u8);   // big-endian
    out.push(bitmask as u8);
    out.extend_from_slice(payload);
    PyBytes::new(py, &out)
}


// ── Header parsing ────────────────────────────────────────────────────────────

/// Parse frame header. Returns (frame_type, bitmask).
/// FULL frames always return bitmask=0.
#[pyfunction]
pub fn delta_parse_header(frame: &[u8]) -> PyResult<(u8, u16)> {
    if frame.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err("Empty delta frame"));
    }
    match frame[0] {
        FRAME_FULL  => Ok((FRAME_FULL, 0)),
        FRAME_DELTA => {
            if frame.len() < 3 {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    format!("DELTA frame too short: {} bytes", frame.len())
                ));
            }
            let bitmask = ((frame[1] as u16) << 8) | (frame[2] as u16);
            Ok((FRAME_DELTA, bitmask))
        }
        t => Err(pyo3::exceptions::PyValueError::new_err(
            format!("Unknown delta frame type: 0x{:02X}", t)
        )),
    }
}

/// Extract payload bytes from a frame (strip header).
#[pyfunction]
pub fn delta_get_payload<'py>(py: Python<'py>, frame: &[u8]) -> PyResult<&'py PyBytes> {
    if frame.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err("Empty frame"));
    }
    match frame[0] {
        FRAME_FULL  => Ok(PyBytes::new(py, &frame[1..])),
        FRAME_DELTA => {
            if frame.len() < 3 {
                return Err(pyo3::exceptions::PyValueError::new_err("DELTA frame too short"));
            }
            Ok(PyBytes::new(py, &frame[3..]))
        }
        _ => Err(pyo3::exceptions::PyValueError::new_err("Unknown frame type")),
    }
}


// ── Bitmask computation ───────────────────────────────────────────────────────

/// Compute u16 bitmask: bit i is set if field_order[i] appears in changed_keys.
/// Maximum 16 fields (u16 bitmask). Fields beyond index 15 are ignored.
#[pyfunction]
pub fn delta_compute_bitmask(field_order: Vec<String>, changed_keys: Vec<String>) -> u16 {
    // Build a small set of changed key indices for O(n) lookup
    let changed: std::collections::HashSet<&str> =
        changed_keys.iter().map(|s| s.as_str()).collect();

    let mut bitmask: u16 = 0;
    for (i, key) in field_order.iter().enumerate() {
        if i >= 16 { break; }
        if changed.contains(key.as_str()) {
            bitmask |= 1u16 << i;
        }
    }
    bitmask
}

/// Expand bitmask back to list of changed field names from field_order.
/// Inverse of delta_compute_bitmask.
#[pyfunction]
pub fn delta_fields_from_bitmask(field_order: Vec<String>, bitmask: u16) -> Vec<String> {
    field_order.into_iter().enumerate()
        .filter(|(i, _)| *i < 16 && (bitmask >> i) & 1 == 1)
        .map(|(_, name)| name)
        .collect()
}

/// Compute change ratio: number of changed fields / total fields.
/// Returns (change_ratio, should_send_full) where should_send_full is True
/// if ratio > threshold or field count > MAX_FIELDS.
#[pyfunction]
pub fn delta_change_ratio(
    total_fields:   usize,
    changed_fields: usize,
    threshold:      f64,
    max_fields:     usize,
) -> (f64, bool) {
    let ratio = if total_fields > 0 {
        changed_fields as f64 / total_fields as f64
    } else {
        1.0
    };
    let force_full = ratio > threshold || total_fields > max_fields;
    (ratio, force_full)
}


// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_full_frame_roundtrip() {
        pyo3::Python::with_gil(|py| {
            let payload = b"hello sensor data";
            let frame = delta_wrap_full(py, payload);
            let bytes = frame.as_bytes();

            assert_eq!(bytes[0], FRAME_FULL);

            let (ftype, bitmask) = delta_parse_header(bytes).unwrap();
            assert_eq!(ftype, FRAME_FULL);
            assert_eq!(bitmask, 0);

            let body = delta_get_payload(py, bytes).unwrap();
            assert_eq!(body.as_bytes(), payload);
        });
    }

    #[test]
    fn test_delta_frame_roundtrip() {
        pyo3::Python::with_gil(|py| {
            let payload  = b"changed_value";
            let bitmask: u16 = 0b0000_0000_0000_0101;  // bits 0 and 2

            let frame = delta_wrap_delta(py, bitmask, payload);
            let bytes = frame.as_bytes();

            assert_eq!(bytes[0], FRAME_DELTA);

            let (ftype, parsed) = delta_parse_header(bytes).unwrap();
            assert_eq!(ftype, FRAME_DELTA);
            assert_eq!(parsed, bitmask);

            let body = delta_get_payload(py, bytes).unwrap();
            assert_eq!(body.as_bytes(), payload);
        });
    }

    #[test]
    fn test_noop_delta() {
        pyo3::Python::with_gil(|py| {
            let frame = delta_wrap_delta(py, 0, b"");
            let bytes = frame.as_bytes();
            assert_eq!(bytes, &[FRAME_DELTA, 0x00, 0x00]);

            let (ftype, bitmask) = delta_parse_header(bytes).unwrap();
            assert_eq!(ftype, FRAME_DELTA);
            assert_eq!(bitmask, 0);
        });
    }

    #[test]
    fn test_bitmask_computation() {
        let field_order = vec![
            "temp".to_string(),
            "co2".to_string(),
            "humidity".to_string(),
            "pressure".to_string(),
        ];
        let changed = vec!["temp".to_string(), "humidity".to_string()];
        let bitmask = delta_compute_bitmask(field_order, changed);
        assert_eq!(bitmask, 0b0101);  // bits 0 (temp) and 2 (humidity)
    }

    #[test]
    fn test_bitmask_empty_changed() {
        let field_order = vec!["a".to_string(), "b".to_string()];
        let bitmask = delta_compute_bitmask(field_order, vec![]);
        assert_eq!(bitmask, 0);
    }

    #[test]
    fn test_fields_from_bitmask() {
        let field_order = vec![
            "temp".to_string(),
            "co2".to_string(),
            "humidity".to_string(),
        ];
        let fields = delta_fields_from_bitmask(field_order, 0b101);
        assert_eq!(fields, vec!["temp", "humidity"]);
    }

    #[test]
    fn test_change_ratio_below_threshold() {
        let (ratio, force_full) = delta_change_ratio(10, 2, 0.5, 16);
        assert!((ratio - 0.2).abs() < 1e-9);
        assert!(!force_full);
    }

    #[test]
    fn test_change_ratio_exceeds_threshold() {
        let (ratio, force_full) = delta_change_ratio(4, 3, 0.5, 16);
        assert!((ratio - 0.75).abs() < 1e-9);
        assert!(force_full);
    }

    #[test]
    fn test_max_fields_forces_full() {
        // 17 fields > max_fields=16 → always send FULL
        let (_, force_full) = delta_change_ratio(17, 1, 0.5, 16);
        assert!(force_full);
    }

    #[test]
    fn test_empty_frame_error() {
        let result = delta_parse_header(&[]);
        assert!(result.is_err());
    }

    #[test]
    fn test_unknown_frame_type_error() {
        let result = delta_parse_header(&[0xFF]);
        assert!(result.is_err());
    }
}
