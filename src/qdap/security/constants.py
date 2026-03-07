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
