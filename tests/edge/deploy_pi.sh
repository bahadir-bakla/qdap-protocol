#!/bin/bash
# Raspberry Pi deploy ve test
# Kullanım: bash tests/edge/deploy_pi.sh pi@192.168.1.100

PI_HOST=${1:-"pi@raspberrypi.local"}
REMOTE_DIR="~/qdap-test"

echo "🍓 Raspberry Pi'ya deploy ediliyor: $PI_HOST"

# Minimal dosyaları kopyala
ssh $PI_HOST "mkdir -p $REMOTE_DIR/src $REMOTE_DIR/tests/edge $REMOTE_DIR/benchmarks/results"

rsync -avz --exclude='.git' --exclude='__pycache__' \
    src/qdap/ $PI_HOST:$REMOTE_DIR/src/qdap/
rsync -avz tests/edge/ $PI_HOST:$REMOTE_DIR/tests/edge/
rsync -avz benchmarks/results/ $PI_HOST:$REMOTE_DIR/benchmarks/results/ 2>/dev/null || true

# Çalıştır
echo "🧪 Testler çalışıyor..."
ssh $PI_HOST "cd $REMOTE_DIR && python3 tests/edge/memory_footprint.py"
ssh $PI_HOST "cd $REMOTE_DIR && python3 tests/edge/cpu_profile.py"

# Sonuçları geri al
rsync -avz $PI_HOST:$REMOTE_DIR/benchmarks/results/memory_footprint.json \
    benchmarks/results/memory_footprint_pi.json

echo "✅ Raspberry Pi test tamamlandı"
echo "📁 Sonuçlar: benchmarks/results/memory_footprint_pi.json"
