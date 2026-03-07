# tests/security/test_encrypted_frame.py

from qdap.security.encrypted_frame import FrameEncryptor


class TestFrameEncryption:

    def test_encrypt_decrypt_roundtrip(self):
        """Şifrele → deşifrele → orijinal."""
        key       = b"\x42" * 32
        enc       = FrameEncryptor(key)
        plaintext = b"Hello QDAP Security!"

        result    = enc.encrypt(plaintext)
        decrypted = enc.decrypt(result.nonce, result.tag, result.ciphertext)

        assert decrypted.verified
        assert decrypted.plaintext == plaintext

    def test_pack_unpack_roundtrip(self):
        """Wire format pack/unpack."""
        key   = b"\x99" * 32
        enc   = FrameEncryptor(key)
        data  = b"QFrame payload data" * 100

        wire     = enc.pack(data)
        result   = enc.unpack(wire)

        assert result.verified
        assert result.plaintext == data

    def test_authentication_failure(self):
        """Tampered ciphertext → verified=False."""
        key  = b"\x11" * 32
        enc  = FrameEncryptor(key)
        data = b"sensitive data"

        wire         = enc.pack(data)
        tampered     = bytearray(wire)
        tampered[-1] ^= 0xFF   # Son byte'ı flip et
        result       = enc.unpack(bytes(tampered))

        assert not result.verified
        assert result.plaintext == b""

    def test_nonce_uniqueness(self):
        """Her şifreleme benzersiz nonce kullanmalı."""
        key    = b"\x55" * 32
        enc    = FrameEncryptor(key)
        data   = b"test"
        nonces = set()

        for _ in range(1000):
            r = enc.encrypt(data)
            nonces.add(r.nonce)

        assert len(nonces) == 1000   # Her nonce benzersiz

    def test_aad_protection(self):
        """AAD (associated data) değişirse doğrulama başarısız."""
        key   = b"\x77" * 32
        enc   = FrameEncryptor(key)
        data  = b"protected payload"
        aad   = b"frame-header-v1"

        result   = enc.encrypt(data, associated_data=aad)
        # Farklı AAD ile deşifrelemeye çalış
        decrypted = enc.decrypt(
            result.nonce, result.tag, result.ciphertext,
            associated_data=b"frame-header-v2"   # Farklı!
        )
        assert not decrypted.verified

    def test_large_payload(self):
        """1MB payload şifreleme."""
        key   = b"\x33" * 32
        enc   = FrameEncryptor(key)
        data  = b"X" * (1024 * 1024)

        wire   = enc.pack(data)
        result = enc.unpack(wire)

        assert result.verified
        assert result.plaintext == data

    def test_wrong_key_fails(self):
        """Yanlış key ile deşifreleme başarısız."""
        key1 = b"\xAA" * 32
        key2 = b"\xBB" * 32
        enc1 = FrameEncryptor(key1)
        enc2 = FrameEncryptor(key2)

        wire   = enc1.pack(b"secret message")
        result = enc2.unpack(wire)

        assert not result.verified
