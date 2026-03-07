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
