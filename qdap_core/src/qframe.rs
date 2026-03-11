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
