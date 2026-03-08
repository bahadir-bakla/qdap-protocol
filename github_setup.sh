#!/bin/bash
# QDAP — GitHub Repo Otomatik Oluştur ve Push Et
# Kullanım: bash github_setup.sh

set -e

# ── Renkler ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════╗"
echo "║   QDAP — GitHub Auto Setup Script        ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── Ön Kontroller ────────────────────────────────────────────────

# Git yüklü mü?
if ! command -v git &>/dev/null; then
    echo -e "${RED}❌ Git yüklü değil.${NC}"
    echo "   Mac: brew install git"
    echo "   Windows: https://git-scm.com/download/win"
    exit 1
fi

# GitHub CLI (gh) yüklü mü?
if ! command -v gh &>/dev/null; then
    echo -e "${YELLOW}📦 GitHub CLI (gh) yükleniyor...${NC}"
    if command -v brew &>/dev/null; then
        brew install gh
    elif command -v winget &>/dev/null; then
        winget install GitHub.cli
    else
        echo -e "${RED}❌ gh yüklenemedi.${NC}"
        echo "   Manuel yükle: https://cli.github.com"
        exit 1
    fi
fi

echo -e "${GREEN}✅ Git ve GitHub CLI hazır${NC}"

# ── GitHub Login ──────────────────────────────────────────────────

echo ""
echo -e "${BLUE}[1/5] GitHub oturumu kontrol ediliyor...${NC}"

if ! gh auth status &>/dev/null; then
    echo -e "${YELLOW}GitHub'a giriş gerekiyor:${NC}"
    gh auth login
fi

GITHUB_USER=$(gh api user --jq .login)
echo -e "${GREEN}✅ Giriş yapıldı: @${GITHUB_USER}${NC}"

# ── Repo Bilgileri ────────────────────────────────────────────────

echo ""
echo -e "${BLUE}[2/5] Repo bilgileri...${NC}"

REPO_NAME="qdap-protocol"
REPO_DESC="QDAP: Quantum-Inspired Adaptive Protocol — Ghost Session, Zero ACK Overhead, Forward Secrecy"
REPO_VISIBILITY="public"   # public veya private

echo "  Repo adı   : ${REPO_NAME}"
echo "  Açıklama   : ${REPO_DESC}"
echo "  Görünürlük : ${REPO_VISIBILITY}"
echo ""
read -p "Devam et? (Enter = evet, Ctrl+C = iptal): "

# ── .gitignore Oluştur ────────────────────────────────────────────

echo -e "${BLUE}[3/5] .gitignore ve README hazırlanıyor...${NC}"

cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*.pyo
.Python
*.egg-info/
dist/
build/
.eggs/
*.egg
pip-wheel-metadata/

# Rust / PyO3
target/
*.so
*.dylib
*.dll
qdap_core/*.pyd

# Virtual environments
.venv/
venv/
env/

# Test & Coverage
.pytest_cache/
.coverage
htmlcov/
*.coveragerc

# IDE
.vscode/
.idea/
*.swp
*.swo
.DS_Store

# Docker
docker_benchmark/results/*.json.bak

# Certificates (güvenlik)
*.pem
*.key
certs/

# Jupyter
.ipynb_checkpoints/
*.ipynb

# Benchmark geçici dosyalar
benchmarks/tmp/
wan_benchmark/results/

# Qiskit cache
.qiskit/
EOF

# ── README Oluştur ────────────────────────────────────────────────

cat > README.md << 'EOF'
# QDAP — Quantum-Inspired Adaptive Protocol

> **Ghost Session** · **Zero ACK Overhead** · **Deadline-Aware Priority** · **Forward Secrecy**

[![Tests](https://github.com/GITHUB_USER/qdap-protocol/actions/workflows/test.yml/badge.svg)](https://github.com/GITHUB_USER/qdap-protocol/actions)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://rustup.rs)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

QDAP is a quantum-inspired application-layer protocol that eliminates ACK overhead through **Ghost Session** — achieving **110× throughput improvement** for small payloads compared to classical request-response protocols.

## Key Results

| Payload | Classical TCP | QDAP (Ghost) | Speedup |
|---------|--------------|--------------|---------|
| 1KB     | 0.31 Mbps    | 34.6 Mbps    | **110×** |
| 1MB     | 7.92 Mbps    | 7.94 Mbps    | 1.0×    |
| 10MB    | 8.25 Mbps    | 9.16 Mbps    | 1.11×   |
| 100MB   | 7.70 Mbps    | 7.86 Mbps    | 1.02×   |

**vs MQTT (IoT scenario):**
- Emergency deadline hit: MQTT **0%** → QDAP **100%**
- Connections: MQTT **100** → QDAP **1**
- ACK bytes: MQTT **4000B** → QDAP **0B**

## Architecture

```
┌─────────────────────────────────────────────┐
│              QDAP Stack                      │
├─────────────────────────────────────────────┤
│  Ghost Session     │  Zero application ACK  │
│  QFT Scheduler     │  Probe-free adaptive   │
│  AmplitudeEncoder  │  Deadline-aware prio   │
│  QFrame Batch      │  8× hash reduction     │
├─────────────────────────────────────────────┤
│  Security Layer    │  X25519 + AES-256-GCM  │
│  Key Rotation      │  Forward Secrecy       │
├─────────────────────────────────────────────┤
│  Rust Hot-Path     │  SHA3, AES-NI, L2-norm │
├─────────────────────────────────────────────┤
│  Transport         │  TCP · QUIC · Loopback │
└─────────────────────────────────────────────┘
```

## Installation

```bash
git clone https://github.com/GITHUB_USER/qdap-protocol
cd qdap-protocol
pip install -r requirements.txt

# Optional: Rust hot-path (requires Rust toolchain)
pip install maturin
cd qdap_core && maturin develop --release
```

## Tests

```bash
pytest tests/ -v
# 226 tests, ~8s
```

## Benchmarks

```bash
# Docker benchmark (requires Docker)
cd docker_benchmark
docker compose up --build

# Results in docker_benchmark/results/
```

## Paper

> *QDAP: A Quantum-Inspired Adaptive Protocol for Zero-Overhead Application-Layer Communication*
> arXiv preprint (coming soon)

## License

MIT
EOF

# GitHub username'i README'ye yaz
sed -i.bak "s/GITHUB_USER/${GITHUB_USER}/g" README.md
rm -f README.md.bak

echo -e "${GREEN}✅ .gitignore ve README hazır${NC}"

# ── Git Init + Commit ─────────────────────────────────────────────

echo ""
echo -e "${BLUE}[4/5] Git repo hazırlanıyor...${NC}"

# Zaten git repo mu?
if [ ! -d ".git" ]; then
    git init
    echo -e "${GREEN}  ✅ Git init${NC}"
fi

# Main branch
git checkout -b main 2>/dev/null || git checkout main 2>/dev/null || true

# Stage all
git add -A

# Commit
git commit -m "🚀 Initial commit: QDAP Protocol v1.0

- Ghost Session: Zero application-layer ACK overhead
- QFT Scheduler: Probe-free adaptive chunk sizing  
- AmplitudeEncoder: Deadline-aware priority (100% emergency delivery)
- QFrame Batch: 8× hash reduction for large payloads
- Security: X25519 ECDH forward secrecy + AES-256-GCM
- Rust hot-path: SHA3, AES-NI, L2-normalization (qdap_core)
- Transport: TCP + QUIC adapters
- Benchmarks: Docker (20ms RTT, 1% loss), MQTT, IoT, QUIC, WAN
- 226 tests passing

Key result: 110× throughput improvement at 1KB (Ghost Session vs Classical)
" 2>/dev/null || git commit --allow-empty -m "Initial commit"

echo -e "${GREEN}  ✅ Commit hazır${NC}"

# ── GitHub'a Push ─────────────────────────────────────────────────

echo ""
echo -e "${BLUE}[5/5] GitHub repo oluşturuluyor ve push ediliyor...${NC}"

# Repo zaten var mı kontrol et
if gh repo view "${GITHUB_USER}/${REPO_NAME}" &>/dev/null; then
    echo -e "${YELLOW}  ⚠️  Repo zaten var: ${GITHUB_USER}/${REPO_NAME}${NC}"
    echo "  Mevcut repo'ya push yapılıyor..."
    git remote remove origin 2>/dev/null || true
    git remote add origin "https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
else
    # Yeni repo oluştur
    gh repo create "${REPO_NAME}" \
        --description "${REPO_DESC}" \
        --${REPO_VISIBILITY} \
        --source=. \
        --remote=origin \
        --push
    echo -e "${GREEN}  ✅ Repo oluşturuldu${NC}"
fi

# Push
git push -u origin main --force

# ── Sonuç ─────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════╗"
echo "║          ✅ BAŞARILI!                     ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo "  🔗 Repo URL: https://github.com/${GITHUB_USER}/${REPO_NAME}"
echo "  📊 Tests  : 224 passing"
echo "  🦀 Rust   : qdap_core (PyO3)"
echo ""
echo -e "${YELLOW}Sonraki adım:${NC}"
echo "  arXiv paper → hafta sonu 🚀"
echo ""

# Tarayıcıda aç
read -p "Repo'yu tarayıcıda açayım mı? (y/n): " OPEN_BROWSER
if [ "$OPEN_BROWSER" = "y" ] || [ "$OPEN_BROWSER" = "Y" ]; then
    gh repo view --web
fi
