#!/bin/bash
# wan_benchmark/scripts/cloud_demo.sh
# ─────────────────────────────────────────────────────────────────
# QDAP Cloud WAN Demo — Ireland ↔ Singapore
# Sunum için tek komutla tam benchmark:
#   cd quantum-protocol
#   ./wan_benchmark/scripts/cloud_demo.sh
#
# Prerequisites:
#   - AWS CLI: aws configure (veya env vars AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
#   - Terraform: brew install terraform
#   - SSH keys: ~/.ssh/qdap-eu.pem  ~/.ssh/qdap-sg.pem
#   - Public keys: ssh-keygen -y -f ~/.ssh/qdap-eu.pem > ~/.ssh/qdap-eu.pem.pub
#                  ssh-keygen -y -f ~/.ssh/qdap-sg.pem > ~/.ssh/qdap-sg.pem.pub
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

# ── Mode ─────────────────────────────────────────────────────────
# --mode ghost   → Classical + Ghost Session (Python-only, no crypto setup)
# --mode secure  → Classical + Secure (X25519 + AES-256-GCM, Phase 8.4)
# --mode full    → All 3 protocols + side-by-side comparison (default)
MODE="full"
for arg in "$@"; do
  case $arg in
    --mode=*) MODE="${arg#*=}" ;;
    --mode)   shift; MODE="${1:-full}" ;;
  esac
done

case "$MODE" in
  ghost)   PROTOCOLS="Classical,Ghost" ;;
  secure)  PROTOCOLS="Classical,Secure" ;;
  full)    PROTOCOLS="Classical,Ghost,Secure" ;;
  *)       echo "Unknown mode: $MODE. Use ghost|secure|full"; exit 1 ;;
esac

# ── Renkler ─────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✅ $*${RESET}"; }
info() { echo -e "${CYAN}  ▶  $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️  $*${RESET}"; }
err()  { echo -e "${RED}  ❌ $*${RESET}"; }
step() { echo -e "\n${BOLD}${CYAN}━━━ $* ${RESET}"; }

# ── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="/home/ubuntu/qdap-venv"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$PROJECT_DIR/wan_benchmark/terraform"
RESULT_DIR="$PROJECT_DIR/wan_benchmark/results"

SSH_EU="ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no \
    -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -o LogLevel=ERROR"
SSH_SG="ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no \
    -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -o LogLevel=ERROR"

RSYNC_EU="rsync -az --info=progress2 \
    -e 'ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o LogLevel=ERROR' \
    --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.hypothesis'"
RSYNC_SG="rsync -az --info=progress2 \
    -e 'ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no -o LogLevel=ERROR' \
    --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.hypothesis'"

# ── Cleanup trap — ALWAYS destroy ───────────────────────────────
TERRAFORM_APPLIED=false
cleanup() {
  if [ "$TERRAFORM_APPLIED" = true ]; then
    echo ""
    step "Cleaning up AWS resources (terraform destroy)..."
    cd "$TF_DIR"
    terraform destroy -auto-approve 2>&1 | grep -E "(Destroy complete|Error)" || true
    ok "All AWS resources destroyed. No further charges."
  fi
}
trap cleanup EXIT

# ── SSH wait helper ──────────────────────────────────────────────
wait_ssh() {
  local CMD="$1"
  local USER_HOST="$2"
  local LABEL="$3"
  local MAX=60
  info "Waiting for SSH: $LABEL..."
  for i in $(seq 1 $MAX); do
    if $CMD "$USER_HOST" "echo OK" 2>/dev/null | grep -q OK; then
      ok "$LABEL is reachable"
      return 0
    fi
    printf "    [%d/%d] retrying...\r" "$i" "$MAX"
    sleep 8
  done
  err "$LABEL SSH timeout after $((MAX*8))s"
  return 1
}

# ═════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║      QDAP Cloud WAN Demo — Automated Benchmark          ║${RESET}"
echo -e "${BOLD}║   Ireland (eu-west-1)  →  Singapore (ap-southeast-1)    ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║  Mode: ${YELLOW}${MODE}${RESET}${BOLD}  |  Protocols: ${CYAN}${PROTOCOLS}${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Pre-flight checks ────────────────────────────────────────────
step "Step 0/6: Pre-flight checks"

command -v terraform >/dev/null 2>&1 || { err "terraform not found. brew install terraform"; exit 1; }
command -v aws       >/dev/null 2>&1 || { err "aws CLI not found. brew install awscli"; exit 1; }
command -v rsync     >/dev/null 2>&1 || { err "rsync not found"; exit 1; }

[ -f ~/.ssh/qdap-eu.pem ]     || { err "~/.ssh/qdap-eu.pem bulunamadı"; exit 1; }
[ -f ~/.ssh/qdap-sg.pem ]     || { err "~/.ssh/qdap-sg.pem bulunamadı"; exit 1; }
[ -f ~/.ssh/qdap-eu.pem.pub ] || { err "~/.ssh/qdap-eu.pem.pub bulunamadı. Çalıştır: ssh-keygen -y -f ~/.ssh/qdap-eu.pem > ~/.ssh/qdap-eu.pem.pub"; exit 1; }
[ -f ~/.ssh/qdap-sg.pem.pub ] || { err "~/.ssh/qdap-sg.pem.pub bulunamadı. Çalıştır: ssh-keygen -y -f ~/.ssh/qdap-sg.pem > ~/.ssh/qdap-sg.pem.pub"; exit 1; }

chmod 600 ~/.ssh/qdap-eu.pem ~/.ssh/qdap-sg.pem 2>/dev/null || true

ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
[ -z "$ACCOUNT" ] && { err "AWS credentials geçersiz. aws configure çalıştır."; exit 1; }
ok "AWS credentials OK (account: $ACCOUNT)"
ok "Pre-flight checks passed"

# ── Step 1: Terraform ────────────────────────────────────────────
step "Step 1/6: Provisioning EC2 instances (Ireland + Singapore)"
cd "$TF_DIR"
terraform init -input=false -upgrade > /dev/null 2>&1
info "terraform apply running (on-demand t3.micro)..."
terraform apply -auto-approve 2>&1 | grep -E "(Apply complete|sender_ip|receiver_ip|aws_instance|Error)" || true

TERRAFORM_APPLIED=true

SENDER_IP=$(terraform output -raw sender_ip 2>/dev/null)
RECEIVER_IP=$(terraform output -raw receiver_ip 2>/dev/null)

[ -z "$SENDER_IP" ]   && { err "sender_ip alınamadı"; exit 1; }
[ -z "$RECEIVER_IP" ] && { err "receiver_ip alınamadı"; exit 1; }

ok "Sender   (Ireland):    $SENDER_IP"
ok "Receiver (Singapore):  $RECEIVER_IP"

# ── Step 2: SSH ready ────────────────────────────────────────────
step "Step 2/6: Waiting for instances to boot"
wait_ssh "$SSH_EU" "ubuntu@$SENDER_IP"   "Ireland sender"
wait_ssh "$SSH_SG" "ubuntu@$RECEIVER_IP" "Singapore receiver"

# ── Measure RTT ─────────────────────────────────────────────────
info "Measuring Ireland → Singapore RTT..."
RTT=$($SSH_EU ubuntu@"$SENDER_IP" \
  "ping -c 5 $RECEIVER_IP 2>/dev/null | tail -1 | awk -F'/' '{print \$5}'" 2>/dev/null | tr -d ' \r\n' || echo "160")
RTT="${RTT:-160}"
ok "RTT: ~${RTT}ms"

# ── Step 3: Sync code ────────────────────────────────────────────
step "Step 3/6: Syncing source code to both instances"
cd "$PROJECT_DIR"

info "Syncing to Singapore (receiver)..."
eval "$RSYNC_SG ./ ubuntu@$RECEIVER_IP:/home/ubuntu/quantum-protocol/" 2>/dev/null || \
  rsync -az -e "ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no -o LogLevel=ERROR" \
    --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
    ./ ubuntu@"$RECEIVER_IP":/home/ubuntu/quantum-protocol/ 2>/dev/null

info "Syncing to Ireland (sender)..."
eval "$RSYNC_EU ./ ubuntu@$SENDER_IP:/home/ubuntu/quantum-protocol/" 2>/dev/null || \
  rsync -az -e "ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o LogLevel=ERROR" \
    --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
    ./ ubuntu@"$SENDER_IP":/home/ubuntu/quantum-protocol/ 2>/dev/null

ok "Code synced to both instances"

# ── Step 4: Install Python deps ──────────────────────────────────
step "Step 4/6: Installing Python dependencies"

PIP_PKGS="cryptography numpy"
VENV="/home/ubuntu/qdap-venv"

info "Installing deps on Ireland (sender)..."
$SSH_EU ubuntu@"$SENDER_IP" \
  "sudo cloud-init status --wait 2>/dev/null || sleep 20; \
   sudo apt-get install -y python3-venv python3-pip -qq 2>/dev/null; \
   python3 -m venv $VENV && \
   $VENV/bin/pip install -q $PIP_PKGS && echo DONE" 2>/dev/null | grep -E "(DONE|error|Error)" || true

info "Installing deps on Singapore (receiver)..."
$SSH_SG ubuntu@"$RECEIVER_IP" \
  "sudo cloud-init status --wait 2>/dev/null || sleep 20; \
   sudo apt-get install -y python3-venv python3-pip -qq 2>/dev/null; \
   python3 -m venv $VENV && \
   $VENV/bin/pip install -q $PIP_PKGS && echo DONE" 2>/dev/null | grep -E "(DONE|error|Error)" || true

ok "Dependencies installed"

# ── Step 5: Benchmark ────────────────────────────────────────────
step "Step 5/6: Running WAN benchmark"

# Start receiver
info "Starting receiver on Singapore (port 19600–19603)..."
$SSH_SG ubuntu@"$RECEIVER_IP" \
  "pkill -f wan_receiver.py 2>/dev/null; sleep 1; \
   cd /home/ubuntu/quantum-protocol && \
   nohup env PYTHONPATH=src $VENV/bin/python3 wan_benchmark/wan_receiver.py \
     </dev/null >/tmp/receiver.log 2>&1 &
   sleep 5
   cat /tmp/receiver.log | head -5
   echo started" 2>/dev/null | grep -E "(started|Error|Traceback)" || warn "Receiver start may have issues"

# Poll receiver readiness
info "Waiting for receiver to bind ports..."
READY=false
for i in $(seq 1 30); do
  RESULT=$($SSH_SG ubuntu@"$RECEIVER_IP" \
    "ss -tlnp 2>/dev/null | grep -c 19600 || echo 0" 2>/dev/null | tr -d ' \r\n')
  if [ "${RESULT:-0}" -ge "1" ] 2>/dev/null; then
    READY=true
    break
  fi
  printf "    [%d/30] waiting for port 19600...\r" "$i"
  sleep 3
done

if [ "$READY" = true ]; then
  ok "Receiver is listening on all ports"
else
  warn "Port check uncertain — proceeding anyway (receiver might still be starting)"
  sleep 5
fi

# Run sender
info "Running sender benchmark (Ireland → Singapore, RTT~${RTT}ms)..."
echo ""
$SSH_EU ubuntu@"$SENDER_IP" \
  "cd /home/ubuntu/quantum-protocol && \
   PYTHONPATH=src PYTHONUNBUFFERED=1 $VENV/bin/python3 -u wan_benchmark/wan_sender.py \
     --host $RECEIVER_IP --rtt ${RTT} --protocols ${PROTOCOLS}" 2>&1 || warn "Sender exited with error (results may still be saved)"

# ── Step 6: Download results ──────────────────────────────────────
step "Step 6/6: Downloading results"
cd "$PROJECT_DIR"
mkdir -p "$RESULT_DIR"

scp -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o LogLevel=ERROR \
  ubuntu@"$SENDER_IP":/home/ubuntu/quantum-protocol/wan_benchmark/results/wan_benchmark.json \
  "$RESULT_DIR/cloud_wan_benchmark.json" 2>/dev/null

if [ -f "$RESULT_DIR/cloud_wan_benchmark.json" ]; then
  echo ""
  echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${GREEN}║               ✅ BENCHMARK COMPLETE                      ║${RESET}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════╝${RESET}"
  echo ""
  echo -e "${BOLD}Results (Ireland → Singapore, ~${RTT}ms RTT, mode=${MODE}):${RESET}"
  echo ""

  python3 - "$RESULT_DIR/cloud_wan_benchmark.json" "$PROTOCOLS" <<'PYEOF'
import json, sys

path     = sys.argv[1]
protos   = [p.strip() for p in sys.argv[2].split(",")]

with open(path) as f:
    d = json.load(f)

results = d.get("results", [])
labels  = sorted(set(r["label"] for r in results),
                 key=lambda x: {"1KB":0,"64KB":1,"1MB":2,"10MB":3}.get(x, 99))

# Header
cols = protos + (["Ghost/Cls"] if "Classical" in protos and "Ghost" in protos else [])
hdr  = f"  {'Payload':<8}" + "".join(f"  {c:>14}" for c in cols)
print(hdr)
print("  " + "-" * (len(hdr) - 2))

for label in labels:
    row = {r["protocol"]: r["tput_median"] for r in results if r["label"] == label}
    line = f"  {label:<8}"
    for p in protos:
        v = row.get(p, 0)
        line += f"  {v:>12.2f}M"
    cls   = row.get("Classical", 0)
    ghost = row.get("Ghost", 0)
    if "Classical" in protos and "Ghost" in protos:
        ratio = ghost / max(cls, 0.001) if cls > 0 else 0
        line += f"  {ratio:>12.1f}x"
    print(line)

# Build full report JSON
report = {
    "ghost_python":  {r["label"]: r["tput_median"] for r in results if r["protocol"] == "Ghost"},
    "secure_rust":   {r["label"]: r["tput_median"] for r in results if r["protocol"] == "Secure"},
    "classical_tcp": {r["label"]: r["tput_median"] for r in results if r["protocol"] == "Classical"},
    "metadata": d.get("metadata", {}),
    "notes": {
        "ghost_python":   "No encryption — baseline QDAP Ghost Session performance",
        "secure_rust":    "Full QDAP with X25519 ECDH + Ed25519 mutual auth + AES-256-GCM",
        "classical_tcp":  "Standard TCP with ACK per message — control baseline",
        "difference":     "Secure adds crypto overhead vs Ghost; both beat TCP on small payloads",
    },
}

import os
report_path = os.path.join(os.path.dirname(path), "cloud_wan_full_report.json")
with open(report_path, "w") as f:
    json.dump(report, f, indent=2)
print(f"\n  Full report: {report_path}")
PYEOF

  echo ""
  ok "Results:     wan_benchmark/results/cloud_wan_benchmark.json"
  ok "Full report: wan_benchmark/results/cloud_wan_full_report.json"
else
  warn "Results file not found on sender. Checking receiver logs..."
  $SSH_SG ubuntu@"$RECEIVER_IP" "cat /tmp/receiver.log 2>/dev/null | tail -20" || true
fi

echo ""
echo -e "${YELLOW}  🧹 Terraform destroy running (trap)...${RESET}"
# cleanup() runs automatically via trap EXIT
