"""
Priority Accuracy Benchmark
==============================

Measures QFrame amplitude encoder's priority ordering accuracy.
Verifies that the most urgent subframe (lowest deadline) is
always placed first in the send order.
"""

from __future__ import annotations

import random
import time

import numpy as np

from qdap.frame.qframe import QFrame, Subframe, SubframeType
from qdap.frame.encoder import AmplitudeEncoder


async def measure_priority_accuracy(n_trials: int = 1000) -> dict:
    """
    QFrame multiplexer'ın öncelik sıralamasını doğrula.

    Test: 3 subframe gönder, alınan sıra amplitude'a uygun mu?
    Senaryo: Video(düşük deadline) + Ses(orta) + Kontrol(acil)
    Beklenti: En acil (en düşük deadline) her zaman ilk sırada
    """
    correct = 0
    wrong = 0
    timings = []

    for trial in range(n_trials):
        video_deadline = random.randint(10, 50)
        audio_deadline = random.randint(5, 15)
        control_deadline = random.randint(1, 5)

        subframes = [
            Subframe(payload=b'V' * 1000, type=SubframeType.DATA,
                     deadline_ms=video_deadline),
            Subframe(payload=b'A' * 100, type=SubframeType.DATA,
                     deadline_ms=audio_deadline),
            Subframe(payload=b'C' * 20, type=SubframeType.CTRL,
                     deadline_ms=control_deadline),
        ]

        t0 = time.monotonic_ns()
        frame = QFrame.create_with_encoder(subframes)
        elapsed = time.monotonic_ns() - t0
        timings.append(elapsed)

        order = frame.send_order
        deadlines = [video_deadline, audio_deadline, control_deadline]

        # send_order'daki ilk eleman en acil olmalı
        if deadlines[order[0]] == min(deadlines):
            correct += 1
        else:
            wrong += 1

    arr = np.array(timings) / 1e6  # → ms

    return {
        "n_trials": n_trials,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / n_trials,
        "encode_p99_ms": float(np.percentile(arr, 99)),
        "encode_mean_ms": float(arr.mean()),
    }
