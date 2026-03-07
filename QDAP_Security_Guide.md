# QDAP — Güvenlik Katmanı Implementation Guide
## Forward Secrecy (X25519 ECDH) + Key Rotation
## Gemini Agent İçin: Tüm Kritik Fonksiyonlar Burada

---

## Neden Bu Kritik?

```
Şu an QDAP'ta:
  ✅ HKDF ile anahtar türetme var
  ✅ SHA3-256 ile frame integrity var
  ❌ Forward secrecy YOK
     → Uzun süreli anahtar ele geçirilirse
       geçmiş tüm trafiği çözebilirsin
  ❌ Key rotation YOK
     → Aynı anahtar sonsuza kadar kullanılıyor
  ❌ Ephemeral key exchange YOK

Reviewer sorar:
  "Ghost Session güvenli mi?"
  Cevabımız şu an: "HKDF var" → zayıf
  Bu guide sonrası: "X25519 ECDH forward secrecy + 
   otomatik key rotation" → güçlü
```

---

## Kriptografi Seçimleri ve Gerekçeleri

```
X25519 (ECDH Curve25519):
  → Ephemeral key exchange için en hızlı, en güvenli
  → TLS 1.3'ün de seçimi
  → NSA Suite B uyumlu
  → Python cryptography kütüphanesi native destekler

HKDF-SHA256:
  → Shared secret'ten oturum anahtarı türetme
  → Zaten QDAP'ta var, entegrasyon kolay

AES-256-GCM:
  → Authenticated encryption (AEAD)
  → Hem şifreleme hem MAC tek seferde
  → Hardware hızlandırma (AES-NI) destekli

Key Rotation:
  → Her N mesaj veya T saniyede bir yeni ephemeral key
  → Rotation sırasında zero-downtime (overlap window)
```

---

## Dosya Yapısı

```
src/qdap/
├── security/                        ← YENİ MODÜL
│   ├── __init__.py
│   ├── handshake.py                 ← X25519 ECDH handshake
│   ├── session_keys.py              ← Oturum anahtarı yönetimi
│   ├── key_rotation.py              ← Otomatik key rotation
│   ├── encrypted_frame.py           ← AES-GCM frame şifreleme
│   └── constants.py                 ← Sabitler
├── frame/
│   └── qframe.py                    ← EncryptedQFrame desteği eklenecek
└── transport/
    └── tcp/
        └── adapter.py               ← Handshake entegrasyonu

tests/security/
├── test_handshake.py
├── test_session_keys.py
├── test_key_rotation.py
└── test_encrypted_frame.py
```

---

## ADIM 1 — Sabitler

```python
# src/qdap/security/constants.py

# Key rotation
KEY_ROTATION_MSG_INTERVAL  = 1000    # Her 1000 mesajda bir rotate
KEY_ROTATION_TIME_INTERVAL = 300.0   # Veya 5 dakikada bir rotate
KEY_OVERLAP_WINDOW         = 50      # Rotation sırasında eski anahtar bu kadar mesaj daha geçerli

# AES-GCM
AES_KEY_SIZE    = 32    # 256 bit
AES_NONCE_SIZE  = 12    # 96 bit (GCM standardı)
AES_TAG_SIZE    = 16    # 128 bit authentication tag

# HKDF
HKDF_HASH       = "sha256"
HKDF_INFO_DATA  = b"qdap-v1-data-key"
HKDF_INFO_CTRL  = b"qdap-v1-ctrl-key"
HKDF_INFO_HMAC  = b"qdap-v1-hmac-key"

# X25519
X25519_KEY_SIZE = 32    # 256 bit

# Wire format
HANDSHAKE_MAGIC      = b"QDAP"   # 4 byte
HANDSHAKE_VERSION    = 0x01      # 1 byte
KEY_ROTATION_MAGIC   = b"RKEY"   # 4 byte
ENCRYPTED_FRAME_FLAG = 0x80      # QFrame flags bit 7 = encrypted
```

---

## ADIM 2 — Handshake (X25519 ECDH) — TAM KOD

```python
# src/qdap/security/handshake.py

"""
X25519 ECDH Ephemeral Handshake.

Protokol:
  1. Client: ephemeral X25519 key pair üret
  2. Client → Server: ClientHello(client_public_key)
  3. Server: ephemeral X25519 key pair üret
  4. Server: shared_secret = X25519(server_private, client_public)
  5. Server → Client: ServerHello(server_public_key)
  6. Client: shared_secret = X25519(client_private, server_public)
  7. Her ikisi: HKDF(shared_secret) → session_key, hmac_key

Forward Secrecy: Her bağlantıda yeni ephemeral key pair.
Eski oturumların anahtarı bellekte tutulmaz.

Wire Format:
  ClientHello: [QDAP(4)][0x01(1)][0x01(1)][public_key(32)] = 38 byte
  ServerHello: [QDAP(4)][0x01(1)][0x02(1)][public_key(32)] = 38 byte
"""

import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from qdap.security.constants import (
    HANDSHAKE_MAGIC,
    HANDSHAKE_VERSION,
    AES_KEY_SIZE,
    HKDF_INFO_DATA,
    HKDF_INFO_HMAC,
)

# Mesaj tipi byte'ları
MSG_CLIENT_HELLO = 0x01
MSG_SERVER_HELLO = 0x02

HELLO_SIZE = 4 + 1 + 1 + 32   # MAGIC + VERSION + MSG_TYPE + PUBLIC_KEY = 38


@dataclass
class SessionKeys:
    """
    ECDH handshake sonucu türetilen oturum anahtarları.
    Her bağlantı için benzersiz — forward secrecy.
    """
    data_key:   bytes    # AES-256-GCM şifreleme anahtarı (32 byte)
    hmac_key:   bytes    # HMAC-SHA256 doğrulama anahtarı (32 byte)
    session_id: bytes    # Oturum kimliği (16 byte, logging için)

    def __repr__(self) -> str:
        return (f"SessionKeys(session_id={self.session_id.hex()[:8]}..., "
                f"data_key=*****, hmac_key=*****)")


def generate_ephemeral_keypair() -> X25519PrivateKey:
    """
    Yeni X25519 ephemeral private key üret.
    Her çağrıda farklı key — ephemeral = tek kullanımlık.
    """
    return X25519PrivateKey.generate()


def serialize_public_key(private_key: X25519PrivateKey) -> bytes:
    """Private key'den 32 byte public key çıkar."""
    return private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )


def deserialize_public_key(raw: bytes) -> X25519PublicKey:
    """32 byte raw public key'i X25519PublicKey nesnesine dönüştür."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    return X25519PublicKey.from_public_bytes(raw)


def compute_shared_secret(
    my_private_key:    X25519PrivateKey,
    peer_public_key:   X25519PublicKey,
) -> bytes:
    """
    X25519 Diffie-Hellman shared secret hesapla.
    Her iki taraf da aynı 32 byte secret'a ulaşır.
    Bu secret'ı doğrudan anahtar olarak KULLANMA — HKDF'e ver.
    """
    return my_private_key.exchange(peer_public_key)


def derive_session_keys(
    shared_secret: bytes,
    salt:          bytes = b"",
) -> SessionKeys:
    """
    HKDF ile shared_secret'tan güvenli oturum anahtarları türet.

    Neden HKDF?
      X25519 çıktısı doğrudan AES anahtarı olarak kullanılamaz —
      dağılımı uniform değil. HKDF bunu düzeltir.

    Args:
        shared_secret: X25519 exchange çıktısı (32 byte)
        salt:          Rastgele salt (replay protection için)

    Returns:
        SessionKeys(data_key, hmac_key, session_id)
    """
    # Data encryption key
    data_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_DATA,
    ).derive(shared_secret)

    # HMAC key
    hmac_key = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt if salt else None,
        info=HKDF_INFO_HMAC,
    ).derive(shared_secret)

    # Session ID (logging/debugging için — güvenlik değeri yok)
    session_id = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt if salt else None,
        info=b"qdap-v1-session-id",
    ).derive(shared_secret)

    return SessionKeys(
        data_key=data_key,
        hmac_key=hmac_key,
        session_id=session_id,
    )


def build_client_hello(private_key: X25519PrivateKey) -> bytes:
    """
    ClientHello mesajı oluştur.
    Format: [QDAP(4)][0x01(1)][0x01(1)][public_key(32)] = 38 byte
    """
    pub = serialize_public_key(private_key)
    return HANDSHAKE_MAGIC + bytes([HANDSHAKE_VERSION, MSG_CLIENT_HELLO]) + pub


def build_server_hello(private_key: X25519PrivateKey) -> bytes:
    """
    ServerHello mesajı oluştur.
    Format: [QDAP(4)][0x01(1)][0x02(1)][public_key(32)] = 38 byte
    """
    pub = serialize_public_key(private_key)
    return HANDSHAKE_MAGIC + bytes([HANDSHAKE_VERSION, MSG_SERVER_HELLO]) + pub


def parse_hello(data: bytes) -> tuple[int, bytes]:
    """
    Hello mesajını parse et.
    Returns: (msg_type, public_key_bytes)
    Raises: ValueError eğer format yanlışsa
    """
    if len(data) < HELLO_SIZE:
        raise ValueError(f"Hello too short: {len(data)} < {HELLO_SIZE}")

    magic    = data[0:4]
    version  = data[4]
    msg_type = data[5]
    pub_key  = data[6:38]

    if magic != HANDSHAKE_MAGIC:
        raise ValueError(f"Invalid magic: {magic!r}")

    if version != HANDSHAKE_VERSION:
        raise ValueError(f"Unsupported version: {version}")

    if msg_type not in (MSG_CLIENT_HELLO, MSG_SERVER_HELLO):
        raise ValueError(f"Unknown message type: {msg_type}")

    return msg_type, pub_key


async def perform_client_handshake(
    reader: "asyncio.StreamReader",
    writer: "asyncio.StreamWriter",
) -> SessionKeys:
    """
    Client tarafında handshake gerçekleştir.

    1. Ephemeral key pair üret
    2. ClientHello gönder
    3. ServerHello bekle
    4. Shared secret hesapla
    5. Session keys türet

    Returns: SessionKeys
    """
    import asyncio

    # Adım 1 + 2: Key pair üret ve gönder
    my_private  = generate_ephemeral_keypair()
    salt        = os.urandom(16)
    client_hello = build_client_hello(my_private)

    # Salt'ı hello'dan önce gönder (replay protection)
    writer.write(salt + client_hello)
    await writer.drain()

    # Adım 3: ServerHello al
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes = parse_hello(raw)

    if msg_type != MSG_SERVER_HELLO:
        raise ValueError("Expected ServerHello, got ClientHello")

    # Adım 4 + 5: Shared secret ve session keys
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)
    session_keys   = derive_session_keys(shared_secret, salt)

    return session_keys


async def perform_server_handshake(
    reader: "asyncio.StreamReader",
    writer: "asyncio.StreamWriter",
) -> SessionKeys:
    """
    Server tarafında handshake gerçekleştir.

    1. ClientHello + salt bekle
    2. Ephemeral key pair üret
    3. Shared secret hesapla
    4. ServerHello gönder
    5. Session keys türet

    Returns: SessionKeys
    """
    import asyncio

    # Adım 1: Salt + ClientHello al
    salt = await asyncio.wait_for(
        reader.readexactly(16),
        timeout=10.0,
    )
    raw = await asyncio.wait_for(
        reader.readexactly(HELLO_SIZE),
        timeout=10.0,
    )
    msg_type, peer_pub_bytes = parse_hello(raw)

    if msg_type != MSG_CLIENT_HELLO:
        raise ValueError("Expected ClientHello, got ServerHello")

    # Adım 2 + 3: Key pair üret, shared secret hesapla
    my_private     = generate_ephemeral_keypair()
    peer_pub       = deserialize_public_key(peer_pub_bytes)
    shared_secret  = compute_shared_secret(my_private, peer_pub)

    # Adım 4: ServerHello gönder
    server_hello = build_server_hello(my_private)
    writer.write(server_hello)
    await writer.drain()

    # Adım 5: Session keys türet
    session_keys = derive_session_keys(shared_secret, salt)

    return session_keys
```

---

## ADIM 3 — AES-GCM Frame Şifreleme — TAM KOD

```python
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

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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

        self._aesgcm  = AESGCM(data_key)
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

        # AESGCM.encrypt → ciphertext + 16 byte tag birleşik
        ct_with_tag = self._aesgcm.encrypt(
            nonce,
            plaintext,
            associated_data if associated_data else None,
        )

        # Tag'i ayır
        ciphertext = ct_with_tag[:-AES_TAG_SIZE]
        tag        = ct_with_tag[-AES_TAG_SIZE:]

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
        from cryptography.exceptions import InvalidTag

        # Tag'i ciphertext'e yeniden ekle (AESGCM.decrypt beklentisi)
        ct_with_tag = ciphertext + tag

        try:
            plaintext = self._aesgcm.decrypt(
                nonce,
                ct_with_tag,
                associated_data if associated_data else None,
            )
            return DecryptionResult(plaintext=plaintext, verified=True)

        except InvalidTag:
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
```

---

## ADIM 4 — Key Rotation — TAM KOD

```python
# src/qdap/security/key_rotation.py

"""
Otomatik key rotation sistemi.

Ne zaman rotate?
  - Her KEY_ROTATION_MSG_INTERVAL mesajda bir (default: 1000)
  - Her KEY_ROTATION_TIME_INTERVAL saniyede bir (default: 300s)
  - Manuel trigger ile

Rotation nasıl çalışır?
  1. Yeni X25519 ephemeral key pair üret
  2. Peer'a KeyRotation mesajı gönder (yeni public key)
  3. Peer yeni shared secret hesaplar, yeni SessionKeys türetir
  4. KEY_OVERLAP_WINDOW mesaj boyunca eski key hâlâ geçerli
     (in-flight mesajlar için)
  5. Overlap window bittikten sonra eski key silinir

Wire format KeyRotation mesajı:
  [RKEY(4)][rotation_id(4)][new_public_key(32)] = 40 byte
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from qdap.security.handshake import (
    generate_ephemeral_keypair,
    serialize_public_key,
    deserialize_public_key,
    compute_shared_secret,
    derive_session_keys,
    SessionKeys,
)
from qdap.security.encrypted_frame import FrameEncryptor
from qdap.security.constants import (
    KEY_ROTATION_MSG_INTERVAL,
    KEY_ROTATION_TIME_INTERVAL,
    KEY_OVERLAP_WINDOW,
    KEY_ROTATION_MAGIC,
)


@dataclass
class RotationState:
    """Bir key rotation döngüsünün durumu."""
    rotation_id:     int
    new_keys:        SessionKeys
    new_encryptor:   FrameEncryptor
    activated_at_msg: int   # Kaçıncı mesajda aktif hale geldi
    old_encryptor:   Optional[FrameEncryptor] = None  # Overlap için
    old_keys:        Optional[SessionKeys]    = None


class KeyRotationManager:
    """
    Otomatik key rotation yöneticisi.

    Kullanım:
        manager = KeyRotationManager(session_keys, writer)
        manager.on_new_keys = my_callback

        # Her mesaj gönderiminde:
        encryptor = manager.current_encryptor
        await manager.maybe_rotate(msg_count)
    """

    def __init__(
        self,
        initial_keys:    SessionKeys,
        writer:          "asyncio.StreamWriter",
        msg_interval:    int   = KEY_ROTATION_MSG_INTERVAL,
        time_interval:   float = KEY_ROTATION_TIME_INTERVAL,
    ):
        self._current_keys      = initial_keys
        self._current_encryptor = FrameEncryptor(initial_keys.data_key)
        self._writer            = writer
        self._msg_interval      = msg_interval
        self._time_interval     = time_interval

        self._msg_count         = 0
        self._last_rotation_ts  = time.monotonic()
        self._rotation_id       = 0
        self._pending_rotation: Optional[RotationState] = None

        # Callback: yeni key hazır olduğunda çağrılır
        self.on_new_keys: Optional[Callable[[SessionKeys], Awaitable[None]]] = None

    @property
    def current_encryptor(self) -> FrameEncryptor:
        return self._current_encryptor

    @property
    def current_keys(self) -> SessionKeys:
        return self._current_keys

    def increment_msg_count(self) -> None:
        self._msg_count += 1

    def should_rotate(self) -> bool:
        """Rotation zamanı geldi mi?"""
        msg_trigger  = (self._msg_count % self._msg_interval == 0
                        and self._msg_count > 0)
        time_trigger = (time.monotonic() - self._last_rotation_ts
                        >= self._time_interval)
        return (msg_trigger or time_trigger) and self._pending_rotation is None

    async def maybe_rotate(self, peer_public_key_provider: "Callable") -> None:
        """
        Rotation gerekiyorsa yeni key pair üret ve peer'a bildir.
        Her mesaj gönderiminde çağrılmalı.
        """
        if not self.should_rotate():
            return
        await self._initiate_rotation(peer_public_key_provider)

    async def _initiate_rotation(self, peer_public_key_provider) -> None:
        """Yeni ephemeral key pair üret ve KeyRotation mesajı gönder."""
        self._rotation_id += 1
        new_private    = generate_ephemeral_keypair()
        my_new_pub     = serialize_public_key(new_private)

        # KeyRotation mesajı gönder
        rotation_msg = (
            KEY_ROTATION_MAGIC
            + self._rotation_id.to_bytes(4, "big")
            + my_new_pub
        )
        self._writer.write(rotation_msg)
        await self._writer.drain()

        # Peer'ın yeni public key'ini al (out-of-band veya callback ile)
        peer_new_pub_bytes = await peer_public_key_provider(self._rotation_id)
        peer_new_pub       = deserialize_public_key(peer_new_pub_bytes)

        # Yeni session keys türet
        new_shared   = compute_shared_secret(new_private, peer_new_pub)
        new_salt     = os.urandom(16)
        new_keys     = derive_session_keys(new_shared, new_salt)
        new_encryptor = FrameEncryptor(new_keys.data_key)

        # Overlap: eski encryptor hâlâ aktif kalır
        self._pending_rotation = RotationState(
            rotation_id=self._rotation_id,
            new_keys=new_keys,
            new_encryptor=new_encryptor,
            activated_at_msg=self._msg_count,
            old_encryptor=self._current_encryptor,
            old_keys=self._current_keys,
        )

        self._last_rotation_ts = time.monotonic()

    def apply_pending_rotation(self) -> bool:
        """
        Overlap window geçtiyse yeni key'e geç.
        Returns: True if rotation applied
        """
        if self._pending_rotation is None:
            return False

        msgs_since = self._msg_count - self._pending_rotation.activated_at_msg
        if msgs_since < KEY_OVERLAP_WINDOW:
            return False

        # Eski key'i bellekten sil (forward secrecy)
        self._current_keys      = self._pending_rotation.new_keys
        self._current_encryptor = self._pending_rotation.new_encryptor

        # Eski key'i temizle
        self._pending_rotation.old_encryptor = None
        self._pending_rotation.old_keys      = None
        self._pending_rotation               = None

        return True

    def get_decryptor_for_rotation(
        self, rotation_id: int
    ) -> Optional[FrameEncryptor]:
        """
        Belirli rotation_id için doğru decryptor'ı döndür.
        Overlap window için eski decryptor'ı da kontrol eder.
        """
        if self._pending_rotation:
            if rotation_id == self._pending_rotation.rotation_id:
                return self._pending_rotation.new_encryptor
            if rotation_id == self._pending_rotation.rotation_id - 1:
                return self._pending_rotation.old_encryptor

        return self._current_encryptor
```

---

## ADIM 5 — Secure Ghost Session Entegrasyonu

```python
# src/qdap/session/secure_ghost_session.py

"""
Mevcut GhostSession'a güvenlik katmanı ekler.
GhostSession'ı subclass eder — mevcut kodu kırmaz.

Değişen tek şey:
  send() → encrypt → send
  receive() → receive → decrypt → verify
"""

from qdap.session.ghost_session import GhostSession
from qdap.security.handshake import (
    perform_client_handshake,
    perform_server_handshake,
    SessionKeys,
)
from qdap.security.encrypted_frame import FrameEncryptor
from qdap.security.key_rotation import KeyRotationManager


class SecureGhostSession(GhostSession):
    """
    Forward secrecy + key rotation ile güvenli Ghost Session.

    Mevcut GhostSession API'si tamamen korunur.
    Caller değişiklik yapmaz — sadece sınıfı değiştirir.

    Kullanım:
        # Eski:
        session = GhostSession(reader, writer)
        # Yeni:
        session = SecureGhostSession(reader, writer)
        await session.perform_handshake(is_client=True)
    """

    def __init__(self, reader, writer, **kwargs):
        super().__init__(reader, writer, **kwargs)
        self._session_keys:    SessionKeys        = None
        self._encryptor:       FrameEncryptor     = None
        self._rotation_mgr:    KeyRotationManager = None
        self._handshake_done:  bool               = False

    async def perform_handshake(self, is_client: bool) -> SessionKeys:
        """
        X25519 ECDH handshake gerçekleştir.
        Bu metodu connect()/accept() sonrası çağır.
        """
        if is_client:
            self._session_keys = await perform_client_handshake(
                self._reader, self._writer
            )
        else:
            self._session_keys = await perform_server_handshake(
                self._reader, self._writer
            )

        self._encryptor     = FrameEncryptor(self._session_keys.data_key)
        self._handshake_done = True
        return self._session_keys

    async def send_secure(self, payload: bytes) -> None:
        """
        Payload'ı şifrele ve gönder.
        Handshake tamamlandıktan sonra kullanılır.
        """
        if not self._handshake_done:
            raise RuntimeError("Handshake not done. Call perform_handshake() first.")

        encrypted = self._encryptor.pack(payload)
        # Length-prefixed gönderim
        import struct
        frame = struct.pack(">I", len(encrypted)) + encrypted
        self._writer.write(frame)
        await self._writer.drain()

    async def recv_secure(self) -> bytes:
        """
        Şifrelenmiş payload al ve deşifrele.
        """
        import struct
        import asyncio

        if not self._handshake_done:
            raise RuntimeError("Handshake not done.")

        # Length prefix oku
        length_bytes = await asyncio.wait_for(
            self._reader.readexactly(4), timeout=30.0
        )
        length = struct.unpack(">I", length_bytes)[0]

        # Encrypted payload oku
        wire_data = await asyncio.wait_for(
            self._reader.readexactly(length), timeout=30.0
        )

        result = self._encryptor.unpack(wire_data)
        if not result.verified:
            raise ValueError("Frame authentication failed — possible tampering!")

        return result.plaintext

    @property
    def session_id(self) -> str:
        if self._session_keys:
            return self._session_keys.session_id.hex()[:16]
        return "not-established"
```

---

## ADIM 6 — Testler — TAM KOD

```python
# tests/security/test_handshake.py

import pytest
from qdap.security.handshake import (
    generate_ephemeral_keypair,
    serialize_public_key,
    deserialize_public_key,
    compute_shared_secret,
    derive_session_keys,
    build_client_hello,
    build_server_hello,
    parse_hello,
    MSG_CLIENT_HELLO,
    MSG_SERVER_HELLO,
    HELLO_SIZE,
)


class TestX25519Handshake:

    def test_keypair_generation(self):
        """Her çağrıda farklı key üretilmeli."""
        k1 = generate_ephemeral_keypair()
        k2 = generate_ephemeral_keypair()
        assert serialize_public_key(k1) != serialize_public_key(k2)

    def test_public_key_size(self):
        """X25519 public key 32 byte olmalı."""
        k   = generate_ephemeral_keypair()
        pub = serialize_public_key(k)
        assert len(pub) == 32

    def test_shared_secret_symmetric(self):
        """İki taraf aynı shared secret'a ulaşmalı."""
        alice_priv = generate_ephemeral_keypair()
        bob_priv   = generate_ephemeral_keypair()

        alice_pub  = deserialize_public_key(serialize_public_key(alice_priv))
        bob_pub    = deserialize_public_key(serialize_public_key(bob_priv))

        alice_secret = compute_shared_secret(alice_priv, bob_pub)
        bob_secret   = compute_shared_secret(bob_priv, alice_pub)

        assert alice_secret == bob_secret

    def test_shared_secret_forward_secrecy(self):
        """Farklı session'lar farklı secret üretmeli."""
        a1 = generate_ephemeral_keypair()
        b1 = generate_ephemeral_keypair()
        a2 = generate_ephemeral_keypair()
        b2 = generate_ephemeral_keypair()

        s1 = compute_shared_secret(a1, deserialize_public_key(serialize_public_key(b1)))
        s2 = compute_shared_secret(a2, deserialize_public_key(serialize_public_key(b2)))

        assert s1 != s2   # Forward secrecy

    def test_session_keys_derivation(self):
        """Session keys deterministik türetilmeli."""
        secret = b"\x42" * 32
        salt   = b"\x01" * 16

        keys1 = derive_session_keys(secret, salt)
        keys2 = derive_session_keys(secret, salt)

        assert keys1.data_key  == keys2.data_key
        assert keys1.hmac_key  == keys2.hmac_key
        assert keys1.session_id == keys2.session_id

    def test_session_keys_different_salt(self):
        """Farklı salt → farklı keys."""
        secret = b"\x42" * 32
        keys1  = derive_session_keys(secret, b"\x01" * 16)
        keys2  = derive_session_keys(secret, b"\x02" * 16)
        assert keys1.data_key != keys2.data_key

    def test_hello_wire_format(self):
        """Hello mesajları doğru formatlanmalı."""
        k = generate_ephemeral_keypair()

        client_hello = build_client_hello(k)
        server_hello = build_server_hello(k)

        assert len(client_hello) == HELLO_SIZE
        assert len(server_hello) == HELLO_SIZE

        msg_type, pub = parse_hello(client_hello)
        assert msg_type == MSG_CLIENT_HELLO
        assert len(pub) == 32

        msg_type, pub = parse_hello(server_hello)
        assert msg_type == MSG_SERVER_HELLO

    def test_full_handshake_simulation(self):
        """Alice-Bob tam handshake simülasyonu."""
        alice_priv = generate_ephemeral_keypair()
        bob_priv   = generate_ephemeral_keypair()

        # Alice ClientHello gönderir
        client_hello = build_client_hello(alice_priv)
        _, alice_pub_bytes = parse_hello(client_hello)

        # Bob ServerHello gönderir
        server_hello = build_server_hello(bob_priv)
        _, bob_pub_bytes = parse_hello(server_hello)

        # Her ikisi shared secret hesaplar
        salt = b"\xAB" * 16
        alice_secret = compute_shared_secret(
            alice_priv, deserialize_public_key(bob_pub_bytes)
        )
        bob_secret = compute_shared_secret(
            bob_priv, deserialize_public_key(alice_pub_bytes)
        )
        assert alice_secret == bob_secret

        alice_keys = derive_session_keys(alice_secret, salt)
        bob_keys   = derive_session_keys(bob_secret, salt)

        assert alice_keys.data_key  == bob_keys.data_key
        assert alice_keys.hmac_key  == bob_keys.hmac_key


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
```

---

## ADIM 7 — Paket Gereksinimleri

```
# requirements.txt'e ekle (zaten olabilir, kontrol et):
cryptography>=41.0.0
```

```bash
# Test et:
python3 -c "
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
print('cryptography OK')
"
```

---

## Teslim Kriterleri

```
✅ src/qdap/security/ modülü oluşturuldu
✅ 5 dosya implement edildi (constants, handshake, encrypted_frame, 
   key_rotation, secure_ghost_session)
✅ tests/security/ altında testler çalışıyor

Test sayısı:
  test_handshake.py:       8 test
  test_encrypted_frame.py: 7 test
  TOPLAM: 15 yeni test

✅ Mevcut 197 test hâlâ geçiyor
✅ Toplam: 197 + 15 = 212+ test geçiyor

Bitince şunu çalıştır ve sonucu gönder:
  pytest tests/security/ -v

212+ test geçmeli.
```

---

## DOKUNMA

```
Şu dosyalara KESİNLİKLE DOKUNMA:
  - src/qdap/session/ghost_session.py  (sadece subclass et)
  - src/qdap/frame/qframe.py
  - Benchmark dosyaları
  - Mevcut testler
  - docker_benchmark/ altındaki her şey

Sadece şunları oluştur:
  - src/qdap/security/ (yeni modül, yeni dosyalar)
  - src/qdap/session/secure_ghost_session.py
  - tests/security/ (yeni test dosyaları)
```
