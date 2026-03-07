// qdap_core/src/x25519.rs

use x25519_dalek::{EphemeralSecret, PublicKey, StaticSecret};
use rand::rngs::OsRng;

/// Yeni ephemeral keypair üret.
/// Returns: (private_key_bytes, public_key_bytes) — her ikisi 32 byte
pub fn generate_keypair() -> ([u8; 32], [u8; 32]) {
    let secret = EphemeralSecret::random_from_rng(OsRng);
    let public = PublicKey::from(&secret);

    // EphemeralSecret consume edilir — bir kez kullanılır
    // Private key'i byte olarak döndürmek için StaticSecret kullan
    let static_secret = StaticSecret::random_from_rng(OsRng);
    let static_public = PublicKey::from(&static_secret);

    (
        static_secret.to_bytes(),
        static_public.to_bytes(),
    )
}

/// X25519 Diffie-Hellman shared secret hesapla.
pub fn diffie_hellman(
    private_key_bytes: &[u8],
    public_key_bytes:  &[u8],
) -> Result<[u8; 32], String> {
    if private_key_bytes.len() != 32 {
        return Err(format!(
            "Private key must be 32 bytes, got {}",
            private_key_bytes.len()
        ));
    }
    if public_key_bytes.len() != 32 {
        return Err(format!(
            "Public key must be 32 bytes, got {}",
            public_key_bytes.len()
        ));
    }

    let mut priv_array = [0u8; 32];
    priv_array.copy_from_slice(private_key_bytes);
    let mut pub_array  = [0u8; 32];
    pub_array.copy_from_slice(public_key_bytes);

    let secret  = StaticSecret::from(priv_array);
    let public  = PublicKey::from(pub_array);
    let shared  = secret.diffie_hellman(&public);

    Ok(shared.to_bytes())
}
