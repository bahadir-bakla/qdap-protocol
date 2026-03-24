import time
import pytest
import qdap.security.session_ticket as st
from qdap.security.session_ticket import SessionTicketStore


def test_ticket_create_and_redeem():
    store = SessionTicketStore()
    session_key = b'\x42' * 32
    wire = store.create_ticket("icu_01", session_key)

    ticket = store.redeem_ticket(wire)
    assert ticket is not None
    assert ticket.session_key == session_key
    assert ticket.device_id == "icu_01"


def test_ticket_single_use():
    """Her ticket sadece bir kez kullanılabilir (replay resistance)."""
    store = SessionTicketStore()
    wire = store.create_ticket("sensor_01", b'\x00' * 32)

    t1 = store.redeem_ticket(wire)
    assert t1 is not None

    t2 = store.redeem_ticket(wire)
    assert t2 is None  # replay → reddedildi


def test_ticket_expiry():
    store = SessionTicketStore()
    old_ttl = st.TICKET_TTL_SECONDS
    st.TICKET_TTL_SECONDS = 0   # anında expire

    wire = store.create_ticket("sensor_02", b'\x01' * 32)
    time.sleep(0.01)
    t = store.redeem_ticket(wire)
    assert t is None

    st.TICKET_TTL_SECONDS = old_ttl


def test_tampered_ticket_rejected():
    store = SessionTicketStore()
    wire = bytearray(store.create_ticket("sensor_03", b'\x02' * 32))
    wire[10] ^= 0xFF   # HMAC bozulacak
    assert store.redeem_ticket(bytes(wire)) is None


def test_evict_expired():
    store = SessionTicketStore()
    old_ttl = st.TICKET_TTL_SECONDS
    st.TICKET_TTL_SECONDS = 0

    for i in range(5):
        store.create_ticket(f"dev_{i}", b'\x03' * 32)

    time.sleep(0.01)
    evicted = store.evict_expired()
    assert evicted == 5

    st.TICKET_TTL_SECONDS = old_ttl


def test_ticket_wire_format_size():
    """Wire ticket boyutu beklenen aralıkta olmalı."""
    store = SessionTicketStore()
    wire = store.create_ticket("dev_x", b'\xAB' * 32)
    # ticket_id(16) + expiry_ms(8) + nonce(12) + enc_key(32+16=48) + hmac(32) = 116
    assert len(wire) == 116, f"Unexpected ticket size: {len(wire)}"


def test_short_wire_rejected():
    """Kısa veri → None döner, exception olmaz."""
    store = SessionTicketStore()
    assert store.redeem_ticket(b'\x00' * 10) is None
    assert store.redeem_ticket(b'') is None


def test_wrong_hmac_rejected():
    """HMAC yanlış olan ticket reddedilmeli."""
    store = SessionTicketStore()
    wire = bytearray(store.create_ticket("dev_h", b'\xCC' * 32))
    # Son 32 byte HMAC — tamamını boz
    wire[-32:] = b'\x00' * 32
    assert store.redeem_ticket(bytes(wire)) is None


def test_valid_ticket_marks_used():
    """Redeem sonrası ticket.used == True olmalı."""
    store = SessionTicketStore()
    wire = store.create_ticket("dev_u", b'\xDD' * 32)
    ticket = store.redeem_ticket(wire)
    assert ticket is not None
    assert ticket.used is True


def test_session_ticket_is_valid_fresh():
    """Yeni oluşturulan ticket is_valid() == True."""
    from qdap.security.session_ticket import SessionTicket
    t = SessionTicket(
        ticket_id=b'\x00' * 16,
        session_key=b'\x00' * 32,
        device_id="test",
        expiry=time.time() + 900,
    )
    assert t.is_valid()


def test_session_ticket_is_valid_expired():
    """Süresi geçmiş ticket is_valid() == False."""
    from qdap.security.session_ticket import SessionTicket
    t = SessionTicket(
        ticket_id=b'\x00' * 16,
        session_key=b'\x00' * 32,
        device_id="test",
        expiry=time.time() - 1,
    )
    assert not t.is_valid()
