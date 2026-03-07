# ─── QDAP Development Container ──────────────────────────
# Quantum-Inspired Dynamic Application Protocol
# Python 3.11 + scientific stack + quantum simulation
# ──────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System dependencies for numpy, scipy, cryptography compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    pkg-config \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Dependencies Layer (cached unless pyproject.toml changes) ───
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir ".[dev]"

# ─── Source Code Layer ───────────────────────────────────
COPY . .

# Install package in editable mode
RUN pip install --no-cache-dir -e ".[dev]"

# ─── Environment ─────────────────────────────────────────
ENV PYTHONPATH=/app/src:$PYTHONPATH
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Default: run tests
CMD ["pytest", "tests/", "-v", "--tb=short"]
