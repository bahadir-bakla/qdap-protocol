#!/usr/bin/env python3
"""
EMA vs BPTT Markov Estimation Karşılaştırması
"""
import sys
import math
import random
import json
import time
import statistics as st
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from qdap.broker.markov_bptt import BPTTMarkovEstimator
from qdap.broker.ghost_session_adaptive import (
    OnlineMarkovEstimator, NETWORK_PROFILES, NetworkType,
    StateTransition, GhostState,
)

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def simulate_channel(n=500, seed=42):
    """Gerçekçi IoT kanal simülasyonu."""
    random.seed(seed)
    obs = []
    p_d_true, p_r_true, q_true = 0.05, 0.80, 0.02

    for i in range(n):
        # Zaman bazlı pattern (gece saatleri → daha fazla ghost)
        hour = (i / n) * 24
        p_d_actual = p_d_true * (1.5 if 2 < hour < 6 else 1.0)

        rtt  = 20 + random.gauss(0, 5) + (50 if hour > 20 else 0)
        loss = 0.01 + (0.1 if hour > 20 else 0) + random.gauss(0, 0.005)
        td   = 5 + random.expovariate(0.2)
        ps   = random.choice([512, 1024, 4096, 65536])

        obs.append({
            "rtt":      max(rtt, 1),
            "loss":     max(loss, 0),
            "payload":  ps,
            "td":       td,
            "p_d_true": p_d_actual,
            "p_r_true": p_r_true,
            "q_true":   q_true,
        })
    return obs


def run_benchmark():
    print("\n=== EMA vs BPTT Markov Benchmark ===\n")
    observations = simulate_channel(n=300)

    # EMA estimator
    profile = NETWORK_PROFILES[NetworkType.STANDARD_IOT]
    ema = OnlineMarkovEstimator(profile)

    # BPTT estimator
    bptt = BPTTMarkovEstimator(seed=42)

    ema_errors, bptt_errors = [], []

    for obs in observations:
        # BPTT: observe
        bptt.observe(obs["rtt"], obs["loss"], obs["payload"], obs["td"])

        # Her ikisi de predict
        ema_p_d, ema_p_r, ema_q = ema.params
        bpt_p_d, bpt_p_r, bpt_q = bptt.predict()

        true_p_d = obs["p_d_true"]
        true_p_r = obs["p_r_true"]
        true_q   = obs["q_true"]

        ema_err  = abs(ema_p_d - true_p_d) + abs(ema_p_r - true_p_r) + abs(ema_q - true_q)
        bptt_err = abs(bpt_p_d - true_p_d) + abs(bpt_p_r - true_p_r) + abs(bpt_q - true_q)

        ema_errors.append(ema_err)
        bptt_errors.append(bptt_err)

        # Update targets
        if random.random() < obs["p_d_true"]:
            tr = StateTransition(GhostState.ACTIVE, GhostState.GHOST)
        else:
            tr = StateTransition(GhostState.ACTIVE, GhostState.ACTIVE)
        ema.update(tr)
        bptt.update_target(obs["p_d_true"], obs["p_r_true"], obs["q_true"])

    print(f"EMA  — Mean Abs Error: {st.mean(ema_errors):.4f} ± {st.stdev(ema_errors):.4f}")
    print(f"BPTT — Mean Abs Error: {st.mean(bptt_errors):.4f} ± {st.stdev(bptt_errors):.4f}")
    bptt_summary = bptt.comparison_summary()
    print(f"\nBPTT training steps: {bptt_summary['train_steps']}")
    print(f"Last loss: {bptt_summary['last_loss']}")

    out = RESULTS_DIR / "bptt_vs_ema.json"
    with open(out, "w") as f:
        json.dump({
            "ema_mean_error":  round(st.mean(ema_errors), 4),
            "bptt_mean_error": round(st.mean(bptt_errors), 4),
            "n_observations":  len(observations),
            "bptt_summary":    bptt_summary,
        }, f, indent=2)
    print(f"\n✅ Kaydedildi: {out}")


if __name__ == "__main__":
    run_benchmark()
