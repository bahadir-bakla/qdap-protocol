# ─── QDAP Makefile ────────────────────────────────────────
# Quick commands for Docker-based development
# ──────────────────────────────────────────────────────────

.PHONY: build test lint format bench shell clean help

IMAGE_NAME = qdap-dev

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

build: ## Build Docker image
	docker compose build qdap-dev

test: ## Run all tests in Docker
	docker compose run --rm qdap-dev pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	docker compose run --rm qdap-dev pytest tests/ -v --tb=short --cov=qdap --cov-report=term-missing

lint: ## Run ruff linter + mypy type check
	docker compose run --rm qdap-dev sh -c "ruff check src/ tests/ && mypy src/qdap/"

format: ## Format code with black + ruff
	docker compose run --rm qdap-dev sh -c "black src/ tests/ && ruff check --fix src/ tests/"

bench: ## Run benchmarks
	docker compose run --rm qdap-bench

shell: ## Open interactive shell in container
	docker compose run --rm qdap-dev /bin/bash

clean: ## Remove containers and images
	docker compose down --rmi local --volumes --remove-orphans

deps-check: ## Verify all dependencies are installed
	docker compose run --rm qdap-dev python -c \
		"import numpy; import scipy; import qiskit; import cryptography; import aioquic; import msgpack; print('✅ All dependencies OK')"
