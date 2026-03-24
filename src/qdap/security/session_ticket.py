"""
Session Ticket — 0-RTT bağlantı resumption için.

TLS 1.3 session ticket mantığı:
  1. Sunucu, başarılı handshake sonrası ticket oluşturur.
  2. Client ticket'ı saklar (device_id bazında).
  3. Tekrar bağlantıda client ticket_id gönderir.
  4. Sunucu ticket'ı doğrular, session key'i restore eder.
  5. Full handshake atlanır → 0-RTT.

Güvenlik:
  - Ticket'lar HMAC-SHA256 ile imzalanır (server master key ile).
  - Her ticket tek kullanımlık (replay protection).
  - TTL: 15 dakika.
  - Ticket encrypt edilir: AES-256-GCM(session_key, server_master_key).
"""

import hashlib
import hmac
import os
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TICKET_TTL_SECONDS = 900   # 15 dakika
TICKET_ID_SIZE     = 16    # bytes
NONCE_SIZE         = 12    # bytes (AES-GCM)
TAG_SIZE           = 16    # bytes


@dataclass
class SessionTicket:
    ticket_id:       bytes   # 16B random ID
    session_key:     bytes   # 32B AES-256 session key
    device_id:       str
    expiry:          float   # unix timestamp
    used:            bool = False

    def is_valid(self) -> bool:
        return not self.used and time.time() < self.expiry

    def mark_used(self):
        self.used = True


class SessionTicketStore:
    """
    Thread-safe session ticket yöneticisi.

    Server tarafında çalışır.
    Her ticket tek kullanımlık (replay resistance).
    """

    def __init__(self):
        import threading
        self._master_key = os.urandom(32)    # Server restart'ta sıfırlanır
        self._aes        = AESGCM(self._master_key)
        self._tickets: Dict[bytes, SessionTicket] = {}
        self._lock = threading.RLock()

    def create_ticket(
        self,
        device_id:   str,
        session_key: bytes,
    ) -> bytes:
        """
        Yeni session ticket oluştur ve serileştir.
        Returns: wire-format ticket bytes
        Wire format: [ticket_id(16)][expiry_ms(8)][nonce(12)][enc_key(32+16)][hmac(32)]
        """
        ticket_id = os.urandom(TICKET_ID_SIZE)
        expiry    = time.time() + TICKET_TTL_SECONDS

        ticket = SessionTicket(
            ticket_id=ticket_id,
            session_key=session_key,
            device_id=device_id,
            expiry=expiry,
        )

        with self._lock:
            self._tickets[ticket_id] = ticket

        # Serialize: [ticket_id(16)][expiry_ms(8)][nonce(12)][enc_key(48)][hmac(32)]
        expiry_ms = int(expiry * 1000)
        nonce     = os.urandom(NONCE_SIZE)
        enc_key   = self._aes.encrypt(nonce, session_key, ticket_id)

        payload = ticket_id + struct.pack(">Q", expiry_ms) + nonce + enc_key
        mac     = hmac.new(
            self._master_key, payload, hashlib.sha256
        ).digest()

        return payload + mac

    def redeem_ticket(self, wire_ticket: bytes) -> Optional[SessionTicket]:
        """
        Client'ın gönderdiği ticket'ı doğrula ve session key'i döndür.
        Geçerli → SessionTicket (tek kullanımlık, sonra invalidated)
        Geçersiz / Expire / Replay → None
        """
        min_size = TICKET_ID_SIZE + 8 + NONCE_SIZE + 32 + 32
        if len(wire_ticket) < min_size:
            return None

        # HMAC doğrula
        payload  = wire_ticket[:-32]
        expected = hmac.new(
            self._master_key, payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(wire_ticket[-32:], expected):
            return None

        # Parse
        ticket_id = wire_ticket[:TICKET_ID_SIZE]
        expiry_ms = struct.unpack_from(">Q", wire_ticket, TICKET_ID_SIZE)[0]
        expiry    = expiry_ms / 1000.0

        if time.time() > expiry:
            return None

        # Decrypt session key
        nonce_start = TICKET_ID_SIZE + 8
        nonce       = wire_ticket[nonce_start:nonce_start + NONCE_SIZE]
        enc_key     = wire_ticket[nonce_start + NONCE_SIZE:-32]

        try:
            session_key = self._aes.decrypt(nonce, enc_key, ticket_id)
        except Exception:
            return None

        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None or not ticket.is_valid():
                return None
            ticket.mark_used()       # tek kullanımlık!
            del self._tickets[ticket_id]
            # Restore session key (stored in memory only)
            ticket.session_key = session_key
            return ticket

    def evict_expired(self) -> int:
        with self._lock:
            expired = [
                k for k, v in self._tickets.items()
                if not v.is_valid()
            ]
            for k in expired:
                del self._tickets[k]
            return len(expired)
