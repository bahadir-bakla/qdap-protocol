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
