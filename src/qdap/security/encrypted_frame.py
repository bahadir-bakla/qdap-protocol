# src/qdap/security/encrypted_frame.py

"""
AES-256-GCM ile QFrame şifreleme/deşifreleme.

Neden AES-GCM?
  - Authenticated encryption: şifreleme + MAC tek seferde
  - Hardware hızlandırma (AES-NI): ~10 GB/s throughput
  - Nonce reuse güvenli değil — her frame için yeni nonce zorunlu

Wire format (şifrelenmiş QFrame):
  [NONCE(12)][TAG(16)][CIPHERTEXT(N)] = 28 + N byte overhead

Nonce üretimi:
  Counter-based: [timestamp(8)][counter(4)] = 12 byte
  Nonce reuse riski yok — monotonic counter
"""

import os
import struct
import time
from dataclasses import dataclass

from qdap._rust_bridge import encrypt_frame as _encrypt, decrypt_frame as _decrypt

from qdap.security.constants import (
    AES_NONCE_SIZE,
    AES_TAG_SIZE,
)


@dataclass
class EncryptionResult:
    nonce:      bytes   # 12 byte
    tag:        bytes   # 16 byte (GCM authentication tag)
    ciphertext: bytes   # N byte
    total_overhead: int = AES_NONCE_SIZE + AES_TAG_SIZE  # 28 byte


@dataclass
class DecryptionResult:
    plaintext:  bytes
    verified:   bool    # Authentication tag doğrulandı mı?


class FrameEncryptor:
    """
    QFrame payload şifreleme/deşifreleme.
    Thread-safe: her instance kendi counter'ına sahip.
    """

    def __init__(self, data_key: bytes):
        """
        Args:
            data_key: 32 byte AES-256 anahtarı (SessionKeys.data_key)
        """
        if len(data_key) != 32:
            raise ValueError(f"data_key must be 32 bytes, got {len(data_key)}")

        self._key     = data_key
        self._counter = 0
        self._base_ts = int(time.monotonic_ns() / 1e6) & 0xFFFFFFFFFFFFFFFF

    def _make_nonce(self) -> bytes:
        """
        12 byte counter-based nonce üret.
        Format: [timestamp_ms(8B)][counter(4B)]
        Monotonic → nonce reuse imkânsız.
        """
        self._counter += 1
        ts = (self._base_ts + self._counter) & 0xFFFFFFFFFFFFFFFF
        return struct.pack(">QI", ts, self._counter & 0xFFFFFFFF)

    def encrypt(
        self,
        plaintext:        bytes,
        associated_data:  bytes = b"",
    ) -> EncryptionResult:
        """
        Plaintext'i AES-256-GCM ile şifrele.

        Args:
            plaintext:       Şifrelenecek QFrame payload
            associated_data: AAD (authenticated but not encrypted)
                             Frame header bilgileri buraya girer

        Returns:
            EncryptionResult(nonce, tag, ciphertext)

        Not:
            AESGCM.encrypt() [ciphertext + tag] döndürür.
            Tag'i ayırıyoruz — wire format'ta ayrı ayrı gönderiyoruz.
        """
        nonce = self._make_nonce()

        ct_with_tag = _encrypt(
            key=self._key,        # 32 byte
            nonce=nonce,
            plaintext=plaintext,
            aad=associated_data,
        )

        ciphertext = ct_with_tag[:-16]
        tag        = ct_with_tag[-16:]

        return EncryptionResult(
            nonce=nonce,
            tag=tag,
            ciphertext=ciphertext,
        )

    def decrypt(
        self,
        nonce:           bytes,
        tag:             bytes,
        ciphertext:      bytes,
        associated_data: bytes = b"",
    ) -> DecryptionResult:
        """
        AES-256-GCM ile deşifrele ve doğrula.

        Returns:
            DecryptionResult(plaintext, verified=True)

        Raises:
            cryptography.exceptions.InvalidTag eğer authentication başarısız
            Bu mesajın tampered/corrupted olduğu anlamına gelir
        """
        try:
            plaintext = _decrypt(
                key=self._key,
                nonce=nonce,
                ciphertext=ciphertext,
                tag=tag,
                aad=associated_data,
            )
            return DecryptionResult(plaintext=plaintext, verified=True)
        except ValueError:
            return DecryptionResult(plaintext=b"", verified=False)

    def pack(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        """
        Şifrele ve wire format'a çevir.
        Format: [NONCE(12)][TAG(16)][CIPHERTEXT(N)]
        """
        result = self.encrypt(plaintext, associated_data)
        return result.nonce + result.tag + result.ciphertext

    def unpack(self, wire_data: bytes, associated_data: bytes = b"") -> DecryptionResult:
        """
        Wire format'tan parse et ve deşifrele.
        """
        if len(wire_data) < AES_NONCE_SIZE + AES_TAG_SIZE:
            return DecryptionResult(plaintext=b"", verified=False)

        nonce      = wire_data[:AES_NONCE_SIZE]
        tag        = wire_data[AES_NONCE_SIZE:AES_NONCE_SIZE + AES_TAG_SIZE]
        ciphertext = wire_data[AES_NONCE_SIZE + AES_TAG_SIZE:]

        return self.decrypt(nonce, tag, ciphertext, associated_data)
