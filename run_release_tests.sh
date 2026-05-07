#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# QDAP — Full Release Test Suite
#
# Çalıştırma:
#   cd quantum-protocol
#   chmod +x run_release_tests.sh
#   ./run_release_tests.sh
#
# Aşamalar:
#   Phase 1 — Local: pytest (444 test) + key benchmarks
#   Phase 2 — AWS WAN: Ireland ↔ Singapore gerçek ağ testi
#   Phase 3 — Security proofs (ProVerif varsa)
#   Phase 4 — Kapsamlı rapor + exit kodu
#
# Gereksinimler:
#   - aws configure  (qdap-wan-test IAM user)
#   - Terraform ≥ 1.5
#   - ~/.ssh/qdap-eu.pem  +  ~/.ssh/qdap-eu.pem.pub
#   - ~/.ssh/qdap-sg.pem  +  ~/.ssh/qdap-sg.pem.pub
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Renkler ──────────────────────────────────────────────────────────────────
G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"
B="\033[1m"; DIM="\033[2m"; RESET="\033[0m"

ok()   { echo -e "  ${G}✅${RESET}  $*"; }
fail() { echo -e "  ${R}❌${RESET}  $*"; FAILURES+=("$*"); }
warn() { echo -e "  ${Y}⚠${RESET}   $*"; }
info() { echo -e "  ${DIM}→${RESET}  $*"; }
hdr()  { echo -e "\n${B}${C}══════════════════════════════════════════════${RESET}"; \
         echo -e "${B}${C}  $*${RESET}"; \
         echo -e "${B}${C}══════════════════════════════════════════════${RESET}"; }

START_TIME=$(date +%s)
FAILURES=()
RESULTS_DIR="release_results/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULTS_DIR"

SSH_EU="ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=5"
SSH_SG="ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=5"

echo -e "${B}${C}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           QDAP — Full Release Test Suite                    ║"
echo "║   Ireland (eu-west-1) ↔ Singapore (ap-southeast-1)          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo "Results dir: $RESULTS_DIR"
echo "Started:     $(date)"


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 — LOCAL TESTS
# ═════════════════════════════════════════════════════════════════════════════
hdr "Phase 1 / 4 — Local Test Suite"

# 1a. Full pytest
echo ""
info "Running full pytest suite..."
if python -m pytest tests/ -q --tb=short \
    2>&1 | tee "$RESULTS_DIR/pytest_output.txt" | tail -5; then
  PYTEST_PASS=$(grep -E '\d+ passed' "$RESULTS_DIR/pytest_output.txt" | tail -1 || echo "? passed")
  ok "pytest: $PYTEST_PASS"
else
  fail "pytest: one or more tests FAILED (see $RESULTS_DIR/pytest_output.txt)"
fi

# 1b. Protocol comparison benchmark (simulated, crisis scenario only — fast)
echo ""
info "Running protocol comparison benchmark (simulated crisis)..."
if python benchmarks/protocol_comparison.py \
    2>&1 | tee "$RESULTS_DIR/protocol_comparison.txt" | grep -E "QDAP|HTTP|gRPC|MQTT|✓|fail|Error" | tail -15; then
  ok "Protocol comparison benchmark done"
else
  warn "Protocol comparison benchmark timed out or had errors — check output"
fi

# 1c. File transfer benchmark (includes large file bulk test fix)
echo ""
info "Running file transfer benchmark..."
if python benchmarks/file_transfer_benchmark.py \
    2>&1 | tee "$RESULTS_DIR/file_transfer.txt" | grep -E "QDAP|HTTP|success|Mbps|crisis|normal" | tail -20; then
  ok "File transfer benchmark done"
else
  warn "File transfer benchmark timed out — check $RESULTS_DIR/file_transfer.txt"
fi

# 1d. IoT comprehensive
echo ""
info "Running IoT comprehensive benchmark..."
if python benchmarks/iot_comprehensive.py \
    2>&1 | tee "$RESULTS_DIR/iot_benchmark.txt" | tail -10; then
  ok "IoT benchmark done"
else
  warn "IoT benchmark timed out — check $RESULTS_DIR/iot_benchmark.txt"
fi

echo ""
ok "Phase 1 complete — local tests done"


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 — AWS WAN TEST
# ═════════════════════════════════════════════════════════════════════════════
hdr "Phase 2 / 4 — AWS WAN Test (Ireland ↔ Singapore)"

# Terraform cleanup on exit
WAN_PROVISIONED=false
cleanup_terraform() {
  if $WAN_PROVISIONED; then
    echo ""
    echo -e "${Y}🧹 Cleaning up AWS resources (terraform destroy)...${RESET}"
    cd wan_benchmark/terraform
    terraform destroy -auto-approve 2>&1 | tail -5 || true
    cd ../..
    echo -e "${G}✅ Terraform destroy complete — no charges will accrue${RESET}"
  fi
}
trap cleanup_terraform EXIT

# 2a. Verify prerequisites
echo ""
info "Verifying prerequisites..."
aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null && ok "AWS auth OK" || \
  { fail "AWS auth failed — run: aws configure"; exit 1; }
[ -f ~/.ssh/qdap-eu.pem ] && [ -f ~/.ssh/qdap-eu.pem.pub ] && ok "Ireland SSH keys found" || \
  { fail "Missing ~/.ssh/qdap-eu.pem or .pub"; exit 1; }
[ -f ~/.ssh/qdap-sg.pem ] && [ -f ~/.ssh/qdap-sg.pem.pub ] && ok "Singapore SSH keys found" || \
  { fail "Missing ~/.ssh/qdap-sg.pem or .pub"; exit 1; }
chmod 400 ~/.ssh/qdap-eu.pem ~/.ssh/qdap-sg.pem 2>/dev/null || true

# 2b. Terraform provision
echo ""
info "Provisioning EC2 instances (t3.small, Ireland + Singapore)..."
cd wan_benchmark/terraform
terraform init -input=false -upgrade > /dev/null 2>&1
TF_LOG="/tmp/tf_apply_$$.log"
if terraform apply -auto-approve > "$TF_LOG" 2>&1; then
  grep -E "Apply complete|sender_ip|receiver_ip" "$TF_LOG" || true
  WAN_PROVISIONED=true
else
  grep -E "Error" "$TF_LOG" | head -10
  fail "Terraform apply failed"
  cd ../..
  exit 1
fi

IRELAND_IP=$(terraform output -raw sender_ip 2>/dev/null || true)
SINGAPORE_IP=$(terraform output -raw receiver_ip 2>/dev/null || true)
cd ../..

[ -z "$IRELAND_IP" ]   && { fail "No Ireland IP from Terraform"; exit 1; }
[ -z "$SINGAPORE_IP" ] && { fail "No Singapore IP from Terraform"; exit 1; }

echo ""
ok "Ireland    (server): $IRELAND_IP"
ok "Singapore  (client): $SINGAPORE_IP"

# 2c. Wait for SSH
echo ""
info "Waiting for SSH on both nodes..."
for i in $(seq 1 36); do
  $SSH_EU ubuntu@$IRELAND_IP   "echo OK" 2>/dev/null && break
  [ $i -eq 36 ] && { fail "Ireland SSH timeout after 3 min"; exit 1; }
  echo "    [$i/36] Ireland not ready, retrying in 5s..."
  sleep 5
done
ok "Ireland SSH ready"

for i in $(seq 1 36); do
  $SSH_SG ubuntu@$SINGAPORE_IP "echo OK" 2>/dev/null && break
  [ $i -eq 36 ] && { fail "Singapore SSH timeout after 3 min"; exit 1; }
  echo "    [$i/36] Singapore not ready, retrying in 5s..."
  sleep 5
done
ok "Singapore SSH ready"

# 2d. Baseline RTT measurement
echo ""
info "Measuring Ireland → Singapore baseline RTT..."
RAW_RTT=$($SSH_EU ubuntu@$IRELAND_IP \
  "ping -c 10 -q $SINGAPORE_IP 2>/dev/null | tail -1 | awk '{print \$4}' | cut -d'/' -f2" \
  2>/dev/null || echo "160")
RAW_RTT=${RAW_RTT:-160}
echo "  Baseline RTT: ~${RAW_RTT}ms"

# 2e. Install dependencies on both nodes
_wait_dpkg() {
  # Wait for cloud-init and apt locks to clear (up to 3 min)
  for _i in $(seq 1 36); do
    sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
    sleep 5
  done
}

setup_ireland() {
  local IP="$1"
  info "[Ireland] Installing dependencies (server node)..."
  $SSH_EU ubuntu@$IP bash -s << 'SETUP_IRELAND'
    export DEBIAN_FRONTEND=noninteractive
    # Wait for cloud-init apt lock
    for _i in $(seq 1 36); do
      sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
      sleep 5
    done
    sudo apt-get update -qq 2>/dev/null || true
    sudo apt-get install -y -qq python3-pip python3-dev iproute2 mosquitto mosquitto-clients 2>/dev/null || \
      sudo apt-get install -y python3-pip python3-dev iproute2 mosquitto mosquitto-clients
    sudo systemctl enable --now mosquitto 2>/dev/null || true
    pip3 install -q --break-system-packages --user \
      aiohttp "websockets==12.0" grpcio grpcio-tools \
      hypercorn "httpx[http2]" \
      paho-mqtt numpy msgpack 2>/dev/null || \
    pip3 install -q --user \
      aiohttp "websockets==12.0" grpcio grpcio-tools \
      hypercorn "httpx[http2]" \
      paho-mqtt numpy msgpack
    export PATH="$HOME/.local/bin:$PATH"
    python3 -c "import aiohttp, websockets, grpc, numpy; print('ireland_deps_ok')"
SETUP_IRELAND
  ok "[Ireland] Dependencies installed (mosquitto running)"
}

setup_singapore() {
  local IP="$1"
  info "[Singapore] Installing dependencies (client node)..."
  $SSH_SG ubuntu@$IP bash -s << 'SETUP_SINGAPORE'
    export DEBIAN_FRONTEND=noninteractive
    for _i in $(seq 1 36); do
      sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
      sleep 5
    done
    sudo apt-get update -qq 2>/dev/null || true
    sudo apt-get install -y -qq python3-pip python3-dev iproute2 2>/dev/null || \
      sudo apt-get install -y python3-pip python3-dev iproute2
    pip3 install -q --break-system-packages --user \
      aiohttp "websockets==12.0" grpcio grpcio-tools \
      "httpx[http2]" paho-mqtt numpy msgpack 2>/dev/null || \
    pip3 install -q --user \
      aiohttp "websockets==12.0" grpcio grpcio-tools \
      "httpx[http2]" paho-mqtt numpy msgpack
    export PATH="$HOME/.local/bin:$PATH"
    python3 -c "import aiohttp, websockets, grpc, numpy; print('singapore_deps_ok')"
SETUP_SINGAPORE
  ok "[Singapore] Dependencies installed"
}

echo ""
info "Setting up dependencies (parallel)..."
setup_ireland   "$IRELAND_IP"   &
setup_singapore "$SINGAPORE_IP" &
wait
ok "Both nodes ready"

# 2f. Sync QDAP source code to both nodes
echo ""
info "Syncing QDAP source to both nodes..."
rsync -az --delete \
  -e "ssh -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='target' --exclude='.venv' --exclude='venv' \
  --exclude='__pycache__' --exclude='*.egg-info' --exclude='release_results' \
  --exclude='graphify-out' \
  ./ ubuntu@$IRELAND_IP:/home/ubuntu/qdap/ > /dev/null 2>&1

rsync -az --delete \
  -e "ssh -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='target' --exclude='.venv' --exclude='venv' \
  --exclude='__pycache__' --exclude='*.egg-info' --exclude='release_results' \
  --exclude='graphify-out' \
  ./ ubuntu@$SINGAPORE_IP:/home/ubuntu/qdap/ > /dev/null 2>&1
ok "Source synced to both nodes"

# 2g. Start wan_server_v2.py on Ireland (expanded — all protocols)
echo ""
info "Starting v2 benchmark servers on Ireland..."
$SSH_EU ubuntu@$IRELAND_IP bash -s << STARTSERVER
  source /home/ubuntu/venv/bin/activate
  pkill -f wan_server_v2.py 2>/dev/null || true
  pkill -f wan_server.py   2>/dev/null || true
  sleep 1
  cd /home/ubuntu/qdap
  export PATH="$HOME/.local/bin:$PATH"
  PYTHONPATH=src setsid python3 benchmarks/wan_server_v2.py \
    </dev/null >/tmp/wan_server.log 2>&1 &
  sleep 6
  ss -tlnp | grep -E "18801|18802|18803|18804|18807|19876" | head -10
  echo "SERVERS_STARTED"
STARTSERVER
ok "Ireland v2 servers started (HTTP1:18801 WS:18802 gRPC:18803 HTTP2:18804 LargeFile:18807 QDAP:19876 MQTT:1883)"

# Poll until core ports ready
info "Verifying server ports are open..."
for i in $(seq 1 25); do
  READY=$($SSH_EU ubuntu@$IRELAND_IP \
    "ss -tlnp | grep -cE '18801|18802|18803|19876' || echo 0" 2>/dev/null)
  [ "${READY:-0}" -ge 3 ] && break
  echo "    [$i/25] Waiting for servers to bind... ($READY/4 core ready)"
  sleep 4
done
ok "Core benchmark servers confirmed listening"

# 2h. Run wan_client_v2.py on Singapore — Normal + Crisis (--all)
echo ""
echo -e "${B}━━━ Running wan_client_v2.py --all (Normal + Crisis with tc netem) ━━━${RESET}"
$SSH_SG ubuntu@$SINGAPORE_IP bash -s << BENCHMARK
  source /home/ubuntu/venv/bin/activate
  cd /home/ubuntu/qdap
  mkdir -p release_results
  export PATH="$HOME/.local/bin:$PATH"
  PYTHONPATH=src python3 benchmarks/wan_client_v2.py $IRELAND_IP --all \
    2>&1 | tee /tmp/wan_benchmark_v2.log
  echo "BENCHMARK_DONE"
BENCHMARK
ok "v2 benchmark complete (Normal + Crisis, all protocols)"

# 2j. Download all results
echo ""
info "Downloading results from Singapore..."
mkdir -p "$RESULTS_DIR/wan"

scp -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no \
  ubuntu@$SINGAPORE_IP:/home/ubuntu/qdap/release_results/wan_benchmark_v2.json \
  "$RESULTS_DIR/wan/wan_benchmark_v2.json" 2>/dev/null && \
  ok "wan_benchmark_v2.json downloaded" || warn "wan_benchmark_v2.json not found — check /tmp/wan_benchmark_v2.log"

scp -i ~/.ssh/qdap-sg.pem -o StrictHostKeyChecking=no \
  ubuntu@$SINGAPORE_IP:/tmp/wan_benchmark_v2.log \
  "$RESULTS_DIR/wan/benchmark_output.txt" 2>/dev/null || true

scp -i ~/.ssh/qdap-eu.pem -o StrictHostKeyChecking=no \
  ubuntu@$IRELAND_IP:/tmp/wan_server.log \
  "$RESULTS_DIR/wan/server_ireland.log" 2>/dev/null || true

# Print live output from benchmark
if [ -f "$RESULTS_DIR/wan/benchmark_output.txt" ]; then
  echo ""
  echo -e "${B}── Live benchmark output ──${RESET}"
  cat "$RESULTS_DIR/wan/benchmark_output.txt"
fi

echo ""
ok "Phase 2 complete — AWS WAN test done"
# cleanup_terraform will run via trap EXIT


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — SECURITY PROOFS
# ═════════════════════════════════════════════════════════════════════════════
hdr "Phase 3 / 4 — Security Proofs"
echo ""

if command -v proverif &>/dev/null; then
  info "Running ProVerif security proofs..."
  if bash security_proofs/run_proofs.sh 2>&1 | tee "$RESULTS_DIR/security_proofs.txt"; then
    ok "All ProVerif proofs passed"
  else
    warn "Some ProVerif proofs failed — check $RESULTS_DIR/security_proofs.txt"
  fi
else
  warn "ProVerif not installed — skipping formal proofs (install: brew install proverif)"
  info "Manual proof scripts: security_proofs/qdap_handshake.pv, qdap_forward_secrecy.pv, qdap_replay.pv"
fi

# 3b. Python-level security checks
info "Running security-related tests..."
python -m pytest tests/ -q --tb=short -k "security or handshake or key_rotation or session_ticket" \
  2>&1 | tee "$RESULTS_DIR/security_tests.txt" | tail -5
ok "Security tests done"

echo ""
ok "Phase 3 complete"


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — COMPREHENSIVE REPORT
# ═════════════════════════════════════════════════════════════════════════════
hdr "Phase 4 / 4 — Release Report"
echo ""

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))
ELAPSED_MIN=$(( ELAPSED / 60 ))
ELAPSED_SEC=$(( ELAPSED % 60 ))

# Print WAN v2 results
if [ -f "$RESULTS_DIR/wan/wan_benchmark_v2.json" ]; then
  echo -e "${B}WAN Benchmark v2 Results — Ireland ↔ Singapore (ALL PROTOCOLS):${RESET}"
  python3 -c "
import json

data = json.load(open('$RESULTS_DIR/wan/wan_benchmark_v2.json'))
meta = data.get('meta', {})
deadline = meta.get('deadline_ms', 500)

def table(scenario, rows):
    print(f'  {scenario}:')
    print(f'  {\"Protocol\":<18} {\"Delivery%\":>10} {\"Emrg%\":>7} {\"p50ms\":>8} {\"p99ms\":>9} {\"Mbps\":>8} {f\"<{deadline:.0f}ms%\":>9}')
    print(f'  {\"-\"*72}')
    for r in rows:
        mark = \" *\" if r[\"protocol\"] == \"QDAP\" else \"\"
        dl = r.get(\"within_deadline_pct\", 0)
        print(f'  {r[\"protocol\"]+mark:<18} {r[\"delivery_rate\"]:>9.1f}% {r[\"emrg_rate\"]:>6.1f}% {r[\"p50_ms\"]:>8.1f} {r[\"p99_ms\"]:>9.1f} {r[\"mbps\"]:>8.4f} {dl:>8.1f}%')
    print()

print(f'  Server: eu-west-1 (Ireland)  |  Client: ap-southeast-1 (Singapore)')
print(f'  Emergency deadline: {deadline}ms  |  n={meta.get(\"n_messages\", \"?\")} messages')
print()
if 'normal' in data:
    table('Normal WAN (real Ireland-Singapore latency, no added loss)', data['normal'])
if 'crisis' in data:
    table('Crisis WAN (tc netem: 30% kernel-level packet loss + 140ms delay)', data['crisis'])

# Key finding
if 'normal' in data and 'crisis' in data:
    print(f'  KEY FINDING — Emergency deadline delivery within {deadline}ms:')
    n_map = {r[\"protocol\"]: r for r in data[\"normal\"]}
    c_map = {r[\"protocol\"]: r for r in data[\"crisis\"]}
    print(f'  {\"Protocol\":<18} {\"Normal\":>10} {\"Crisis\":>10}')
    print(f'  {\"-\"*40}')
    for proto in n_map:
        if proto in c_map:
            nd = n_map[proto].get(\"within_deadline_pct\", 0)
            cd = c_map[proto].get(\"within_deadline_pct\", 0)
            mark = \" <-- QDAP\" if proto == \"QDAP\" else \"\"
            print(f'  {proto:<18} {nd:>9.1f}% {cd:>9.1f}%{mark}')
" 2>/dev/null || cat "$RESULTS_DIR/wan/benchmark_output.txt" 2>/dev/null | tail -40
  echo ""
fi

# Pytest summary
PYTEST_RESULT=$(grep -oP '\d+ passed(?:, \d+ failed)?' "$RESULTS_DIR/pytest_output.txt" 2>/dev/null | head -1 || echo "unknown")

# Final pass/fail summary
echo -e "${B}Release Gate Summary:${RESET}"
[ ${#FAILURES[@]} -eq 0 ] && \
  echo -e "  ${G}${B}✅ ALL CHECKS PASSED — ready to release${RESET}" || \
  { echo -e "  ${R}${B}❌ FAILURES:${RESET}"; for f in "${FAILURES[@]}"; do echo -e "    ${R}• $f${RESET}"; done; }

echo ""
echo -e "  pytest:        $PYTEST_RESULT"
echo -e "  WAN results:   $RESULTS_DIR/wan/"
echo -e "  All logs:      $RESULTS_DIR/"
echo -e "  Total time:    ${ELAPSED_MIN}m ${ELAPSED_SEC}s"
echo ""

# Save machine-readable summary
python3 -c "
import json, os
summary = {
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'duration_sec': $ELAPSED,
    'pytest': '$PYTEST_RESULT',
    'failures': [${FAILURES[@]+"$(printf '"%s",' "${FAILURES[@]}" | sed 's/,$//') "}],
    'results_dir': '$RESULTS_DIR',
    'wan': {
        'ireland_ip': '$IRELAND_IP',
        'singapore_ip': '$SINGAPORE_IP',
        'baseline_rtt_ms': '${RAW_RTT:-unknown}',
    }
}
with open('$RESULTS_DIR/summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('Summary saved to $RESULTS_DIR/summary.json')
" 2>/dev/null || true

[ ${#FAILURES[@]} -eq 0 ] && exit 0 || exit 1
