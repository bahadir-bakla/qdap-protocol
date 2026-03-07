#!/usr/bin/env python3
"""
Self-signed TLS sertifikası üretir.
QUIC için TLS 1.3 zorunlu — sertifikasız çalışmaz.
"""

import datetime
import pathlib
import ipaddress

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate():
    # RSA 2048 private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "qdap-quic-test"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QDAP"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(
            datetime.datetime.utcnow() + datetime.timedelta(days=3650)
        )
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("qdap-quic-receiver"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("172.20.0.10")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    out = pathlib.Path(__file__).parent / "certs"
    out.mkdir(exist_ok=True)

    # Private key kaydet
    (out / "key.pem").write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    # Certificate kaydet
    (out / "cert.pem").write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    print(f"✅ Sertifika üretildi: {out}/cert.pem + {out}/key.pem")


if __name__ == "__main__":
    generate()
