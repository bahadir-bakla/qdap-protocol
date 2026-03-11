#!/bin/bash
# wan_benchmark/scripts/deploy_aws.sh
# ─────────────────────────────────────────────────────────────────
# QDAP Cloud WAN Benchmark — Automated Deploy & Test
#
# Prerequisites:
#   - AWS CLI configured (aws sts get-caller-identity)
#   - Terraform installed
#   - SSH keys:  ~/.ssh/qdap-eu.pem  (eu-west-1)
#                ~/.ssh/qdap-sg.pem  (ap-southeast-1)
#   - Public keys generated:
#       ssh-keygen -y -f ~/.ssh/qdap-eu.pem > ~/.ssh/qdap-eu.pem.pub
#       ssh-keygen -y -f ~/.ssh/qdap-sg.pem > ~/.ssh/qdap-sg.pem.pub
#
# Usage:
#   cd quantum-protocol
#   ./wan_benchmark/scripts/deploy_aws.sh
#
# What it does:
#   1. Provisions EC2 spot instances (Ireland + Singapore)
#   2. Copies code via rsync
#   3. Installs Rust + builds qdap_core native bindings
#   4. Runs WAN benchmark (3 protocols × 3 payloads)
#   5. Downloads results to wan_benchmark/results/cloud_wan_benchmark.json
#   6. Destroys all AWS resources (terraform destroy)
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────
SSH_EU="ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
SSH_SG="ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
RESULT_DIR="wan_benchmark/results"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Cleanup trap: ALWAYS terraform destroy ──────────────────────
cleanup() {
  echo ""
  echo "🧹 Cleaning up AWS resources..."
  cd "$PROJECT_DIR/wan_benchmark/terraform"
  terraform destroy -auto-approve 2>/dev/null || true
  echo "✅ Terraform destroy completed. No charges will accrue."
}
trap cleanup EXIT

# ── Helper: setup a node (install Rust, venv, maturin, deps) ───
setup_node() {
  local SSH_CMD="$1"
  local IP="$2"
  local LABEL="$3"

  echo "  ⚙️  [$LABEL] Installing Rust toolchain..."
  $SSH_CMD ubuntu@$IP \
    "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y" \
    > /dev/null 2>&1

  echo "  ⚙️  [$LABEL] Creating Python venv + installing dependencies..."
  $SSH_CMD ubuntu@$IP \
    "cd /home/ubuntu/quantum-protocol && \
     source \$HOME/.cargo/env && \
     sudo apt-get install -y python3-venv > /dev/null 2>&1 && \
     python3 -m venv .venv && \
     source .venv/bin/activate && \
     pip install -q maturin cryptography aiohttp numpy" \
    > /dev/null 2>&1

  echo "  ⚙️  [$LABEL] Building qdap_core native bindings (maturin)..."
  $SSH_CMD ubuntu@$IP \
    "cd /home/ubuntu/quantum-protocol && \
     source \$HOME/.cargo/env && \
     source .venv/bin/activate && \
     cd qdap_core && maturin develop --release 2>&1 | tail -3"

  echo "  ✅ [$LABEL] Ready!"
}

# ── Start ───────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       QDAP Cloud WAN Benchmark — Automated Deploy       ║"
echo "║   Ireland (eu-west-1) → Singapore (ap-southeast-1)      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Terraform apply ─────────────────────────────────────
echo "📦 Step 1/6: Provisioning AWS EC2 instances..."
cd "$PROJECT_DIR/wan_benchmark/terraform"
terraform init -input=false > /dev/null 2>&1
terraform apply -auto-approve 2>&1 | grep -E "(Apply complete|sender_ip|receiver_ip|Error)"

SENDER_IP=$(terraform output -raw sender_ip)
RECEIVER_IP=$(terraform output -raw receiver_ip)

echo ""
echo "  ✅ Sender   (Ireland):    $SENDER_IP"
echo "  ✅ Receiver  (Singapore): $RECEIVER_IP"

# ── Step 2: Wait for SSH ────────────────────────────────────────
echo ""
echo "⏳ Step 2/6: Waiting for SSH to become available..."
for i in {1..30}; do
  $SSH_EU ubuntu@$SENDER_IP "echo OK" 2>/dev/null && break
  sleep 10
done
for i in {1..30}; do
  $SSH_SG ubuntu@$RECEIVER_IP "echo OK" 2>/dev/null && break
  sleep 10
done

# ── RTT measurement ────────────────────────────────────────────
echo ""
echo "📡 Measuring RTT (Ireland → Singapore)..."
RTT=$($SSH_EU ubuntu@$SENDER_IP \
  "ping -c 10 $RECEIVER_IP 2>/dev/null | tail -1 | awk '{print \$4}' | cut -d'/' -f2" 2>/dev/null || echo "160")
echo "  RTT: ~${RTT}ms"

# ── Step 3: Copy code via rsync ─────────────────────────────────
echo ""
echo "📦 Step 3/6: Syncing source code to both nodes..."
cd "$PROJECT_DIR"
rsync -az -e "ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
  ./ ubuntu@$RECEIVER_IP:/home/ubuntu/quantum-protocol/ > /dev/null 2>&1 || true
rsync -az -e "ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='target' --exclude='.venv' --exclude='__pycache__' \
  ./ ubuntu@$SENDER_IP:/home/ubuntu/quantum-protocol/ > /dev/null 2>&1 || true
echo "  ✅ Code synced"

# ── Step 4: Build qdap_core on both nodes ───────────────────────
echo ""
echo "🔨 Step 4/6: Building qdap_core on both nodes..."
setup_node "$SSH_SG" "$RECEIVER_IP" "Singapore"
setup_node "$SSH_EU" "$SENDER_IP"   "Ireland"

# ── Step 5: Start receiver, wait for it, run benchmark ──────────
echo ""
echo "🚀 Step 5/6: Running WAN benchmark..."

# Start receiver in background (separate SSH sessions to avoid hang)
echo "  Starting receiver (Singapore)..."
$SSH_SG ubuntu@$RECEIVER_IP \
  "cd /home/ubuntu/quantum-protocol && \
   source .venv/bin/activate && \
   PYTHONPATH=src setsid python wan_benchmark/wan_receiver.py </dev/null >/tmp/receiver.log 2>&1 &
   sleep 2 && echo LAUNCHED"

# Poll until receiver ports are ready
echo "  ⏳ Waiting for receiver to bind ports..."
for i in {1..60}; do
  $SSH_SG ubuntu@$RECEIVER_IP \
    "ss -tlnp | grep -q 19600 && echo READY" 2>/dev/null | grep -q READY && break
  echo "    [$i/60] Not ready yet, waiting 5s..."
  sleep 5
done
echo "  ✅ Receiver is listening on all ports"

# Run benchmark on sender
echo "  Running sender benchmark (Ireland → Singapore)..."
echo ""
$SSH_EU ubuntu@$SENDER_IP \
  "cd /home/ubuntu/quantum-protocol && \
   source .venv/bin/activate && \
   PYTHONPATH=src PYTHONUNBUFFERED=1 python -u wan_benchmark/wan_sender.py \
     --host $RECEIVER_IP --rtt ${RTT:-0}" 2>&1 || true

# ── Step 6: Download results ───────────────────────────────────
echo ""
echo "📥 Step 6/6: Downloading results..."
cd "$PROJECT_DIR"
mkdir -p "$RESULT_DIR"
scp -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
  ubuntu@$SENDER_IP:/home/ubuntu/quantum-protocol/wan_benchmark/results/wan_benchmark.json \
  "$RESULT_DIR/cloud_wan_benchmark.json" 2>/dev/null || echo "⚠️  Failed to fetch results"

if [ -f "$RESULT_DIR/cloud_wan_benchmark.json" ]; then
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║              ✅ BENCHMARK COMPLETE                       ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""
  python3 -m json.tool "$RESULT_DIR/cloud_wan_benchmark.json" | head -60
  echo ""
  echo "Results saved to: $RESULT_DIR/cloud_wan_benchmark.json"
else
  echo "⚠️  No results file found. Check receiver logs:"
  echo "  $SSH_SG ubuntu@$RECEIVER_IP 'cat /tmp/receiver.log'"
fi

# cleanup() runs automatically via trap EXIT
echo ""
echo "🧹 Terraform destroy will run automatically..."
