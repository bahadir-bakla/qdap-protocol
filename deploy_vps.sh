#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# QDAP VPS Deploy Script
# Kullanım: ./deploy_vps.sh <VPS_IP> <DOMAIN> [SSH_USER]
# Örnek:    ./deploy_vps.sh 1.2.3.4 qdap.dev root
#
# Yapar:
#   1. VPS'e Node.js + Nginx + Certbot kurar
#   2. React website'i build eder ve gönderir
#   3. QDAP Python server'ı kurar (systemd service)
#   4. Nginx reverse proxy config'i yazar
#   5. SSL sertifikası alır (Let's Encrypt)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

VPS_IP="${1:-}"
DOMAIN="${2:-}"
SSH_USER="${3:-root}"

[ -z "$VPS_IP" ] || [ -z "$DOMAIN" ] && {
  echo "Usage: ./deploy_vps.sh <VPS_IP> <DOMAIN> [SSH_USER]"
  echo "Example: ./deploy_vps.sh 1.2.3.4 qdap.dev root"
  exit 1
}

SSH="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15"
SCP="scp -o StrictHostKeyChecking=no"

G="\033[92m"; B="\033[1m"; C="\033[96m"; RESET="\033[0m"
ok()   { echo -e "  ${G}✅${RESET}  $*"; }
info() { echo -e "  → $*"; }

echo -e "${B}${C}"
echo "╔══════════════════════════════════════════════════╗"
echo "║         QDAP VPS Deploy — $DOMAIN"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo "VPS: $VPS_IP  |  Domain: $DOMAIN  |  User: $SSH_USER"

# ── 1. Website build ──────────────────────────────────────────────────────────
info "Building React website..."
cd website
npm install --silent
npm run build
cd ..
ok "Website built → website/dist/"

# ── 2. VPS: system deps ───────────────────────────────────────────────────────
info "Installing system dependencies on VPS..."
$SSH $SSH_USER@$VPS_IP bash -s << 'SYSDEPS'
  export DEBIAN_FRONTEND=noninteractive
  for _i in $(seq 1 12); do
    sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
    sleep 5
  done
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    nginx certbot python3-certbot-nginx \
    python3-pip python3-dev \
    mosquitto mosquitto-clients \
    iproute2 curl
  sudo systemctl enable --now nginx
  sudo systemctl enable --now mosquitto
  echo "sysdeps_ok"
SYSDEPS
ok "System deps installed (nginx, certbot, mosquitto)"

# ── 3. Python deps on VPS ────────────────────────────────────────────────────
info "Installing Python deps on VPS..."
$SSH $SSH_USER@$VPS_IP bash -s << 'PYDEPS'
  pip3 install -q --break-system-packages \
    aiohttp "websockets==12.0" grpcio \
    hypercorn "httpx[http2]" \
    paho-mqtt numpy msgpack 2>/dev/null || \
  pip3 install -q \
    aiohttp "websockets==12.0" grpcio \
    hypercorn "httpx[http2]" \
    paho-mqtt numpy msgpack
  python3 -c "import aiohttp, websockets; print('py_deps_ok')"
PYDEPS
ok "Python deps installed"

# ── 4. Upload QDAP source ────────────────────────────────────────────────────
info "Uploading QDAP source to VPS..."
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='target' --exclude='.venv' \
  --exclude='__pycache__' --exclude='*.egg-info' \
  --exclude='release_results' --exclude='graphify-out' \
  --exclude='node_modules' \
  ./ $SSH_USER@$VPS_IP:/opt/qdap/
ok "Source uploaded to /opt/qdap/"

# ── 5. Upload website dist ───────────────────────────────────────────────────
info "Uploading website..."
$SSH $SSH_USER@$VPS_IP "sudo mkdir -p /var/www/qdap && sudo chown -R $SSH_USER /var/www/qdap"
rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=no" \
  website/dist/ $SSH_USER@$VPS_IP:/var/www/qdap/
ok "Website uploaded to /var/www/qdap/"

# ── 6. QDAP server systemd service ───────────────────────────────────────────
info "Setting up QDAP server as systemd service..."
$SSH $SSH_USER@$VPS_IP bash -s << SYSTEMD
  cat > /tmp/qdap-server.service << 'EOF'
[Unit]
Description=QDAP Benchmark Server
After=network.target mosquitto.service

[Service]
Type=simple
User=$SSH_USER
WorkingDirectory=/opt/qdap
Environment=PYTHONPATH=/opt/qdap/src
ExecStart=/usr/bin/python3 /opt/qdap/benchmarks/wan_server_v2.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo mv /tmp/qdap-server.service /etc/systemd/system/qdap-server.service
  sudo systemctl daemon-reload
  sudo systemctl enable qdap-server
  sudo systemctl restart qdap-server
  sleep 3
  sudo systemctl status qdap-server --no-pager | head -5
  echo "service_ok"
SYSTEMD
ok "QDAP server running as systemd service"

# ── 7. Nginx config ──────────────────────────────────────────────────────────
info "Configuring Nginx..."
$SSH $SSH_USER@$VPS_IP bash -s << NGINX
  cat > /tmp/qdap-nginx.conf << 'EOF'
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    root /var/www/qdap;
    index index.html;

    # React SPA — tüm route'ları index.html'e yönlendir
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # QDAP stats API proxy
    location /api/stats {
        proxy_pass http://127.0.0.1:18900/stats;
        proxy_set_header Host \$host;
        add_header Access-Control-Allow-Origin *;
    }

    # WebSocket proxy
    location /ws {
        proxy_pass http://127.0.0.1:18802;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Gzip
    gzip on;
    gzip_types text/plain text/css application/javascript application/json;

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
  sudo mv /tmp/qdap-nginx.conf /etc/nginx/sites-available/$DOMAIN
  sudo ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t
  sudo systemctl reload nginx
  echo "nginx_ok"
NGINX
ok "Nginx configured for $DOMAIN"

# ── 8. SSL (Let's Encrypt) ───────────────────────────────────────────────────
info "Getting SSL certificate (Let's Encrypt)..."
echo ""
echo "  ⚠  DNS'i kontrol et: $DOMAIN → $VPS_IP olarak ayarlanmış olmalı"
echo "  Devam etmek için Enter'a bas, atlamak için Ctrl+C..."
read -r

$SSH $SSH_USER@$VPS_IP \
  "sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --non-interactive --agree-tos -m admin@$DOMAIN --redirect" && \
  ok "SSL sertifikası alındı! HTTPS aktif." || \
  echo "  ⚠  SSL alınamadı — DNS henüz yayılmamış olabilir. Sonra: sudo certbot --nginx -d $DOMAIN"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}${G}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${B}${G}║  DEPLOY TAMAMLANDI!                              ║${RESET}"
echo -e "${B}${G}║  https://$DOMAIN                       ║${RESET}"
echo -e "${B}${G}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo "  QDAP server:  systemctl status qdap-server"
echo "  Nginx logs:   tail -f /var/log/nginx/access.log"
echo "  Server logs:  journalctl -u qdap-server -f"
echo ""
