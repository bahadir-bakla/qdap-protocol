#!/bin/bash
# Self-signed cert for nginx HTTP/2 testing
mkdir -p tests/real_servers/certs
openssl req -x509 -newkey rsa:2048 -keyout tests/real_servers/certs/server.key \
  -out tests/real_servers/certs/server.crt -days 365 -nodes \
  -subj "/C=TR/ST=Istanbul/L=Istanbul/O=QDAP/CN=localhost"
echo "✅ Sertifikalar oluşturuldu"
