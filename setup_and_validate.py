#!/usr/bin/env python3
"""
Tek komutla kurulum ve doğrulama.
python setup_and_validate.py
"""
import subprocess
import sys
from pathlib import Path

G="\033[92m"; R="\033[91m"; Y="\033[93m"; RESET="\033[0m"

checks = [
    ("Python >= 3.11",   lambda: sys.version_info >= (3, 11)),
    ("src/qdap mevcut",  lambda: Path("src/qdap").exists()),
    ("tests/ mevcut",    lambda: Path("tests").exists()),
    ("benchmarks/ mevcut", lambda: Path("benchmarks").exists()),
]

module_checks = [
    "src.qdap.scheduler.qft_scheduler",
    "src.qdap.broker.ghost_session_adaptive",
    "src.qdap.compression.delta_encoder",
    "src.qdap.transport.parallel_sender",
    "src.qdap.security.session_ticket",
    "src.qdap.broker.markov_bptt",
]

print("\n🔍 QDAP System Validation\n" + "═"*40)

all_pass = True
for name, check in checks:
    try:
        ok = check()
        sym = f"{G}✅{RESET}" if ok else f"{R}❌{RESET}"
        print(f"  {sym} {name}")
        if not ok:
            all_pass = False
    except Exception as e:
        print(f"  {R}❌{RESET} {name}: {e}")
        all_pass = False

print("\nModül testleri:")
sys.path.insert(0, str(Path.cwd()))
for mod in module_checks:
    try:
        __import__(mod)
        print(f"  {G}✅{RESET} {mod}")
    except ImportError as e:
        print(f"  {R}❌{RESET} {mod}: {e}")
        all_pass = False

print("\nTest suite:")
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
    capture_output=True, text=True
)
if result.returncode == 0:
    lines = result.stdout.strip().split("\n")
    print(f"  {G}✅{RESET} {lines[-1]}")
else:
    print(f"  {R}❌{RESET} Test failures detected")
    print(result.stdout[-500:])
    all_pass = False

print("\n" + "═"*40)
if all_pass:
    print(f"{G}✅ Sistem hazır. Paper sonuçları üretilebilir.{RESET}")
else:
    print(f"{R}❌ Bazı kontroller başarısız. make setup çalıştır.{RESET}")
    sys.exit(1)
