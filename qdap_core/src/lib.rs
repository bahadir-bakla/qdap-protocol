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
