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
