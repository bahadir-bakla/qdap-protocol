#!/bin/bash
# examples/proxy_demo.sh
# Senaryo: curl → QDAP Proxy → nginx
#
# Kurulum:
#   docker run -d -name qdap-nginx -p 8083:80 nginx
#   python -m qdap.proxy.proxy_server \
#       --listen-port 8080 \
#       --qdap-host localhost \
#       --qdap-port 19601 \
#       --mode client &
#   python -m qdap.proxy.proxy_server \
#       --listen-port 8082 \
#       --qdap-host localhost \
#       --qdap-port 19601 \
#       --target-host localhost \
#       --target-port 8083 \
#       --mode server &
#
# Test:
#   curl http://localhost:8080/          # Normal HTTP
#   curl -H "Content-Type: audio/mpeg" http://localhost:8080/  # High priority
#   curl -H "X-QDAP-Priority: 999" http://localhost:8080/      # Emergency

echo "=== QDAP HTTP Proxy Demo Script ==="
echo "If servers are running, testing connectivity:"
echo "Normal:    $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/)"
echo "Audio:     $(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: audio/mpeg' http://localhost:8080/)"
echo "Emergency: $(curl -s -o /dev/null -w '%{http_code}' -H 'X-QDAP-Priority: 999' http://localhost:8080/)"
