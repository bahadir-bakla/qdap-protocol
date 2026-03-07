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
