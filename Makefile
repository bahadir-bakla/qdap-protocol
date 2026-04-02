# ─── QDAP Makefile ────────────────────────────────────────
# Commands for development, testing, and benchmarking
# ──────────────────────────────────────────────────────────

.PHONY: build test lint format bench shell clean help setup validate benchmark paper docker-build docker-test docker-benchmark docker-servers benchmark-quick benchmark-wan test-fast test-coverage

IMAGE_NAME = qdap-dev
PYTHON = python3
PYTEST = pytest
PIP    = pip3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Docker (legacy) ──────────────────────────────────────────────────────────

build: ## Build Docker image
	docker compose build qdap-dev

test: ## Run all tests (local or Docker)
	$(PYTEST) tests/ -v --tb=short -q

test-cov: ## Run tests with coverage report (Docker)
	docker compose run --rm qdap-dev pytest tests/ -v --tb=short --cov=qdap --cov-report=term-missing

test-fast: ## Run tests skipping slow statistical tests
	$(PYTEST) tests/ -x --tb=short -q --ignore=tests/test_statistical.py

test-coverage: ## Run tests with coverage report (local)
	$(PYTEST) tests/ --cov=src/qdap --cov-report=html --tb=short

lint: ## Run ruff linter + mypy type check
	docker compose run --rm qdap-dev sh -c "ruff check src/ tests/ && mypy src/qdap/"

format: ## Format code with black + ruff
	docker compose run --rm qdap-dev sh -c "black src/ tests/ && ruff check --fix src/ tests/"

bench: ## Run benchmarks (Docker)
	docker compose run --rm qdap-bench

shell: ## Open interactive shell in container
	docker compose run --rm qdap-dev /bin/bash

clean: ## Remove containers, images, and temp files
	docker compose down --rmi local --volumes --remove-orphans 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

deps-check: ## Verify all dependencies are installed
	docker compose run --rm qdap-dev python -c \
		"import numpy; import scipy; import qiskit; import cryptography; import aioquic; import msgpack; print('✅ All dependencies OK')"

# ── Setup ────────────────────────────────────────────────────────────────────

setup: ## Install Python dependencies locally
	@echo "📦 Bağımlılıklar kuruluyor..."
	$(PIP) install --break-system-packages \
		pytest pytest-asyncio \
		cryptography pynacl \
		paho-mqtt "httpx[http2]" websockets \
		matplotlib numpy \
		maturin
	@echo "🦀 Rust build..."
	cd src && maturin develop --release 2>/dev/null || echo "Rust build atlandı (Python fallback aktif)"
	@echo "✅ Setup tamamlandı"

# ── Validation ────────────────────────────────────────────────────────────────

validate: ## Validate all modules load correctly
	@echo "🔍 Sistem doğrulaması..."
	$(PYTHON) -c "import sys; print(f'Python {sys.version}')"
	$(PYTHON) -c "from src.qdap.scheduler.qft_scheduler import QFTScheduler; print('✅ QFT Scheduler')"
	$(PYTHON) -c "from src.qdap.broker.ghost_session_adaptive import AdaptiveGhostSession; print('✅ Ghost Session')"
	$(PYTHON) -c "from src.qdap.compression.delta_encoder import DeltaEncoder; print('✅ Delta Compression')"
	$(PYTHON) -c "from src.qdap.transport.parallel_sender import plan_parallel_chunks; print('✅ Parallel Sender')"
	$(PYTHON) -c "from src.qdap.broker.markov_bptt import BPTTMarkovEstimator; print('✅ BPTT Markov')"
	@echo "✅ Tüm modüller yüklendi"

# ── Benchmarks ───────────────────────────────────────────────────────────────

benchmark: ## Run all benchmarks (local)
	@echo "📊 Benchmarklar çalışıyor..."
	$(PYTHON) benchmarks/protocol_comparison.py
	$(PYTHON) benchmarks/ablation_study.py
	$(PYTHON) benchmarks/statistical_analysis.py
	@echo "✅ Tüm benchmarklar tamamlandı"
	@echo "📁 Sonuçlar: benchmarks/results/"

benchmark-quick: ## Run protocol comparison + ablation only
	$(PYTHON) benchmarks/protocol_comparison.py
	$(PYTHON) benchmarks/ablation_study.py

benchmark-wan: ## Run AWS WAN benchmark (requires credentials)
	@echo "🌐 AWS WAN benchmark (10 dakika)..."
	bash wan_benchmark/scripts/cloud_demo.sh

# ── Paper Figures ─────────────────────────────────────────────────────────────

paper: ## Generate paper figures
	@echo "📈 Paper figürleri üretiliyor..."
	$(PYTHON) benchmarks/protocol_comparison.py
	$(PYTHON) benchmarks/visualize_comparison.py
	$(PYTHON) benchmarks/ablation_study.py
	@echo "✅ Figürler: benchmarks/results/*.png"

# ── Docker Real Servers ───────────────────────────────────────────────────────

docker-build: ## Build QDAP Docker image
	docker build -t qdap:latest .

docker-test: ## Run tests in Docker
	docker run --rm qdap:latest make test

docker-benchmark: ## Run benchmarks in Docker
	docker run --rm -v $(PWD)/benchmarks/results:/app/benchmarks/results \
		qdap:latest make benchmark

docker-servers: ## Start real server comparison (nginx, mosquitto, QDAP)
	bash tests/real_servers/gen_certs.sh
	docker compose -f docker-compose.real-servers.yml up -d
	sleep 10
	$(PYTHON) benchmarks/real_server_benchmark.py
	docker compose -f docker-compose.real-servers.yml down
