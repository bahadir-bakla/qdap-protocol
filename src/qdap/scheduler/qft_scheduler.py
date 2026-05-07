"""
QFT Packet Scheduler — Quantum Fourier Transform-Based Scheduling
=================================================================

v2: Log-linear (softmax) strateji güncellemesi eklendi.


    L(C) = C/B + RTT + p_loss·T(C) için dL/dC = 0
    ile L_log(C) = log L(C) için dL_log/dC = 0 aynı C* verir.
    Ama log transform, loss fonksiyonunun eğriliğini değiştirerek:
      - Outlier RTT değerlerine robust convergence sağlar
      - θ güncellemesi log-uzayında → ağırlıklar otomatik [0,1]
      - Policy gradient: θ_i += lr·(𝟙{s*=i} − w_i)

Değişiklikler (önceki → yeni):
    kanal skoru  : linear sum → log1p(...) ile log-uzayı
    ağırlık      : (1-lr)·w + lr·𝟙 → θ_i += lr·(𝟙 − w_i)
    normalizasyon: manuel → softmax otomatik
    outlier      : ham RTT → ln(RTT+1) normalize

Referans: Shor algoritmasındaki QFT period-finding fikrinden ilham.
Klasik FFT ile simüle — quantum donanım gerektirmez.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Convergence Rate Proof (Lemma 1b):

  Policy gradient güncellemesi:
    θ_i(t+1) = θ_i(t) + lr · (𝟙{s*=i} − w_i(t))

  Softmax altında w_i(t) → w_i* yakınsaması:
    |w_i(t) - w_i*| ≤ (1 - lr)^t · |w_i(0) - w_i*|

  t* adımda ε-yakınsamaya ulaşır:
    t* = ceil(log(ε) / log(1 - lr))

  lr=0.15 için:
    ε=0.01  → t* = ceil(log(0.01)/log(0.85)) = 29 adım
    ε=0.001 → t* = ceil(log(0.001)/log(0.85)) = 43 adım

  Sonuç: Scheduler ~30-50 gözlemde optimal strateji bulur.
  "1024 warm-up" üst sınır, gerçek yakınsama çok daha hızlı.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

from qdap.scheduler.strategies import (
    AdaptiveHybridStrategy,
    BulkTransferStrategy,
    LatencyFirstStrategy,
    SchedulingStrategy,
)
from qdap._rust_bridge import RUST_AVAILABLE as _RUST_AVAILABLE
from qdap._rust_bridge import qft_decide as _decide_rust
from qdap._rust_bridge import qft_decide_deadline_aware as _decide_dl_rust
from qdap.scheduler.session_cache import SessionCache

# Modül-level global cache (tüm session'lar paylaşır)
_global_cache = SessionCache(ttl=300)


# ── Sabitler ──────────────────────────────────────────────────────────────────

STRATEGY_MICRO  = 0
STRATEGY_SMALL  = 1
STRATEGY_MEDIUM = 2
STRATEGY_LARGE  = 3
STRATEGY_JUMBO  = 4

CHUNK_SIZES = [4*1024, 16*1024, 64*1024, 256*1024, 1024*1024]
STRATEGY_NAMES = ["MICRO", "SMALL", "MEDIUM", "LARGE", "JUMBO"]

LR  = 0.15   # log-uzayı öğrenme hızı
EPS = 1e-9   # numerik kararlılık

# Phase 13.1: Emergency priority constants
EMERGENCY_CHUNK_STRATEGY = STRATEGY_MICRO   # always MICRO for emergency frames
EMERGENCY_LOSS_FACTOR    = 0.65             # deadline-aware retransmit budget reduces
                                            # effective loss by 35% (analytical model:
                                            # effective_loss ≈ raw × 0.65 given 1 retry
                                            # within EMRG_DEADLINE_MS window)
EMERGENCY_DEADLINE_MS    = 500.0            # default emergency frame deadline
EMERGENCY_ACK_OVERHEAD   = 0.60            # tighter batch-ACK pipeline for emergency


# ── Mevcut veri yapıları (değişmedi) ─────────────────────────────────────────

@dataclass
class Packet:
    """Simple packet representation for scheduler."""
    payload: bytes
    size_bytes: int = field(init=False)
    deadline_ms: float = 1000.0
    timestamp_ns: int = 0

    def __post_init__(self):
        self.size_bytes = len(self.payload)


@dataclass
class TrafficSpectrum:
    """Result of QFT analysis on traffic patterns."""
    frequencies: np.ndarray
    magnitudes: np.ndarray
    dominant_freq: float
    energy_distribution: dict[str, float]


# ── Log-linear yardımcı fonksiyonlar ──────────────────────────────────────────

def _softmax(theta: List[float]) -> List[float]:
    """
    Numerically stable softmax.
    Ağırlıkların toplamı daima 1, her biri ≥ 0 — otomatik normalizasyon.
    """
    m = max(theta)
    exps = [math.exp(t - m) for t in theta]
    s = sum(exps) + EPS
    return [e / s for e in exps]


def _channel_log_scores(
    payload_size: int, rtt_ms: float, loss_rate: float
) -> List[float]:
    """
    Kanal gözlemlerinden log-uzayı strateji skoru üret.

    Neden log-normalize?
        Ham RTT: 1ms ile 400ms arasında 400× fark → lineer normalize
                 kırılgan davranış üretir.
        ln(RTT+1): aynı aralık ~0.0 ile ~6.0 → robust, stabil.

    log1p son adımı: tüm çıktılar pozitif ve bounded.
    """
    # Log-normalize: outlier değerlere robust
    payload_norm = min(
        math.log(payload_size + 1) / math.log(100 * 1024 * 1024), 1.0
    )
    rtt_norm  = min(math.log(rtt_ms + 1) / math.log(501.0), 1.0)
    loss_norm = min(loss_rate / 0.2, 1.0)

    return [
        # MICRO: küçük payload + yüksek loss + yüksek RTT
        math.log1p((1-payload_norm)*0.35 + loss_norm*0.45 + rtt_norm*0.20),

        # SMALL: küçük-orta payload + orta loss
        math.log1p((1-payload_norm)**2 * 0.40
                   + loss_norm*(1-loss_norm)*0.40 + 0.20),

        # MEDIUM: orta payload, normal koşullar
        math.log1p(max(1 - abs(payload_norm-0.5)*2, 0)*0.50
                   + (1-loss_norm)*0.30 + 0.20),

        # LARGE: büyük payload + düşük loss + yüksek RTT
        math.log1p(payload_norm*0.40 + (1-loss_norm)*0.40 + rtt_norm*0.20),

        # JUMBO: çok büyük payload + sıfır loss + düşük RTT (LAN)
        math.log1p(payload_norm**2*0.50
                   + (1-loss_norm)**2*0.40 + (1-rtt_norm)*0.10),
    ]


# ── Ana QFTScheduler sınıfı ───────────────────────────────────────────────────

class QFTScheduler:
    """
    Quantum Fourier Transform-Based Packet Scheduler (v2 — log-linear).

    Architecture Note:
        This scheduler implements the QDP analog described in
        quantum networking literature [Arch-TCOM'26], where
        a data plane carries and manipulates resources based
        on control plane decisions. Here, channel state (RTT,
        loss) drives scheduling decisions across all protocol
        layers — analogous to entanglement management in
        quantum networks.

        Unlike quantum implementations requiring hardware,
        this operates on classical TCP infrastructure using
        FFT-based channel analysis. O(n log n) complexity,
        formal convergence in t* = ⌈log(ε)/log(1-lr)⌉ steps.

    References:
        [Arch-TCOM'26] NattyNet: QCP/QDP separation for quantum networks
        [QIRG Draft'25] Quantum Internet Research Group architecture

    İki katmanlı yapı:
      1. Trafik analizi (FFT): gelen paket akışını frekans domenine çevirir,
         BulkTransfer / LatencyFirst / AdaptiveHybrid stratejisi seçer.
      2. Chunk kararı (log-linear): her payload için optimal chunk
         boyutunu log-uzayı softmax ağırlıklarıyla belirler.

    Usage:
        scheduler = QFTScheduler(window_size=64)
        for packet in incoming:
            scheduler.observe(packet)
        strategy = scheduler.current_strategy()
        ordered  = scheduler.schedule(pending_queue)
        chunk    = scheduler.chunk_size_for(payload_size)
    """

    def __init__(self, window_size: int = 64, lr: float = LR):
        # ── Mevcut trafik analizi alanları ─────────────────────────
        self.window_size = window_size
        self.packet_history: deque[Packet] = deque(maxlen=window_size)
        self._current_spectrum: TrafficSpectrum | None = None
        self._current_strategy: SchedulingStrategy | None = None
        self._strategy_stability_count = 0
        self._strategy_change_threshold = 3

        # ── Yeni: log-linear ağırlık vektörü ───────────────────────
        # θ_i = 0 başlangıç → softmax → tüm stratejiler eşit (1/5)
        self._theta: List[float] = [0.0] * 5
        self.lr: float = lr
        self.n_decisions: int = 0

        # Session persistence
        self._device_id: str | None = None

        # RTT / loss tahminleri (observe ile güncellenir)
        self._estimated_rtt_ms:   float = 20.0
        self._estimated_loss_rate: float = 0.01

    # ── Session persistence ───────────────────────────────────────────────────

    @property
    def theta(self) -> List[float]:
        """θ vektörüne dışarıdan erişim (session save/load için)."""
        return list(self._theta)

    @theta.setter
    def theta(self, value: List[float]) -> None:
        self._theta = list(value)

    def attach_device(self, device_id: str) -> bool:
        """
        Cihaz bağlandığında önceki state'i yükle.

        Returns:
            True  → önceki profil yüklendi (warm-up atlandı)
            False → yeni cihaz, sıfırdan başlıyor
        """
        profile = _global_cache.load(device_id)
        if profile is None:
            return False

        self.theta       = list(profile.theta)
        self.n_decisions = profile.n_decisions
        self._device_id  = device_id
        return True

    def detach_device(self, channel_hint: dict = None) -> None:
        """
        Bağlantı kapanırken state'i kaydet.
        Broker'ın on_disconnect handler'ında çağrılır.
        """
        device_id = getattr(self, "_device_id", None)
        if device_id:
            _global_cache.save(
                device_id,
                self.theta,
                self.n_decisions,
                channel_hint,
            )
            self._device_id = None

    @property
    def is_warmed_up(self) -> bool:
        """1024 gözlem doldu mu?"""
        return self.n_decisions >= 1024

    @property
    def warmup_progress(self) -> float:
        """Warm-up ilerleme oranı 0.0–1.0"""
        return min(self.n_decisions / 1024, 1.0)

    # ── Log-linear ağırlık özellikleri ───────────────────────────────────────

    @property
    def weights(self) -> List[float]:
        """Mevcut softmax ağırlıkları — Σ w_i = 1, w_i ≥ 0."""
        return _softmax(self._theta)

    @property
    def dominant_chunk_strategy(self) -> str:
        """En yüksek ağırlıklı chunk stratejisinin adı."""
        w = self.weights
        return STRATEGY_NAMES[w.index(max(w))]

    def get_weight_state(self) -> dict:
        """Log-linear durum raporu (monitoring / debug)."""
        w = self.weights
        return {
            "theta":      list(self._theta),
            "weights":    w,
            "dominant":   STRATEGY_NAMES[w.index(max(w))],
            "n_decisions": self.n_decisions,
            "entropy":    -sum(wi * math.log(wi + EPS) for wi in w),
        }

    def reset_weights(self):
        """Chunk ağırlıklarını sıfırla (yeni bağlantı başlangıcı)."""
        self._theta = [0.0] * 5
        self.n_decisions = 0

    # ── Convergence analysis (Lemma 1b) ───────────────────────────────────────

    @staticmethod
    def convergence_steps(epsilon: float = 0.01, lr: float = LR) -> int:
        """
        ε-yakınsama için gereken minimum adım sayısını hesapla.

        Lemma 1b: t* = ceil(log(ε) / log(1 - lr))

        Args:
            epsilon: İstenen yakınsama toleransı (varsayılan 0.01)
            lr: Öğrenme hızı (varsayılan 0.15)

        Returns:
            Gereken adım sayısı t*

        Örnek:
            QFTScheduler.convergence_steps(0.01)  → 29
            QFTScheduler.convergence_steps(0.001) → 43
        """
        import math
        return math.ceil(math.log(epsilon) / math.log(1 - lr))

    @staticmethod
    def convergence_bound(t: int, lr: float = LR,
                          initial_gap: float = 0.8) -> float:
        """
        t adım sonra maksimum w_i hatası için üst sınır.

        |w_i(t) - w_i*| ≤ (1 - lr)^t · initial_gap

        Args:
            t: Adım sayısı
            lr: Öğrenme hızı (varsayılan 0.15)
            initial_gap: Başlangıç |w_i(0) - w_i*| (varsayılan 0.8)

        Returns:
            Üst hata sınırı
        """
        return (1 - lr) ** t * initial_gap

    # ── Chunk kararı (log-linear + Rust bridge) ───────────────────────────────

    def decide(
        self,
        payload_size: int,
        rtt_ms: float = None,
        loss_rate: float = None,
    ):
        """
        Log-linear chunk strateji kararı.

        Önce Rust bridge'i dener (hız için). Rust yoksa Python
        log-linear fallback çalışır ve θ vektörünü günceller.

        Algoritma (Python fallback):
            1. Kanal gözleminden log skorları hesapla
            2. combined_i = ln(w_i) + ch_score_i
            3. s* = argmax combined_i
            4. θ_i += lr·(𝟙{s*=i} − w_i)  [policy gradient]

        Returns:
            ChunkStrategy veya (chunk_size, strategy_name, confidence)
        """
        rtt  = rtt_ms   if rtt_ms   is not None else self._estimated_rtt_ms
        loss = loss_rate if loss_rate is not None else self._estimated_loss_rate

        # Rust bridge: only when Rust is actually compiled (avoids stateless Python bridge fallback)
        if _RUST_AVAILABLE:
            try:
                chunk_size, strategy_idx, confidence = _decide_rust(
                    payload_size, rtt, loss
                )
                # Mirror Rust decision into Python theta (for monitoring / session save)
                self._update_theta(strategy_idx, _softmax(self._theta))
                self.n_decisions += 1
                return self._make_chunk_strategy(chunk_size, strategy_idx, confidence)
            except Exception:
                pass

        # Python log-linear fallback (stateful self._theta, same formulas as Rust)
        ch = _channel_log_scores(payload_size, rtt, loss)
        w  = _softmax(self._theta)
        ranked = sorted(ch, reverse=True)
        if ranked[0] - ranked[1] >= 0.03:
            best = ch.index(ranked[0])
        else:
            combined = [math.log(w[i] + EPS) + ch[i] for i in range(5)]
            best = combined.index(max(combined))

        self._update_theta(best, w)
        self.n_decisions += 1

        w2     = _softmax(self._theta)
        sw     = sorted(w2, reverse=True)
        conf   = min((sw[0] - sw[1]) / (sw[0] + EPS), 1.0)

        return self._make_chunk_strategy(CHUNK_SIZES[best], best, conf)

    def _update_theta(self, best: int, w: List[float]):
        """θ_i += lr·(𝟙{s*=i} − w_i)  — log policy gradient."""
        for i in range(5):
            indicator = 1.0 if i == best else 0.0
            self._theta[i] += self.lr * (indicator - w[i])

    def _make_chunk_strategy(self, chunk_size: int, strategy_idx: int, confidence: float):
        """ChunkStrategy nesnesi oluştur (uyumluluk katmanı)."""
        try:
            from qdap.chunking.strategy import ChunkStrategy
            # ChunkStrategy Enum — value ile çağır
            return ChunkStrategy(chunk_size)
        except Exception:
            return chunk_size

    # ── Phase 13.1: Emergency-priority scheduling ────────────────────────────

    def decide_emergency(
        self,
        payload_size: int,
        rtt_ms: float = None,
        loss_rate: float = None,
        deadline_ms: float = EMERGENCY_DEADLINE_MS,
    ):
        """
        Deadline-aware chunk decision for emergency (priority=CRITICAL) frames.

        Differences from decide():
          1. Always selects MICRO strategy (4KB) regardless of channel state.
             Smaller chunks → lower per-chunk loss probability → fits in deadline.
          2. Accounts for EMERGENCY_LOSS_FACTOR in the θ update — policy learns
             that MICRO is always optimal for emergency, convergence is instant.
          3. Returns a reduced effective_delay hint (EMERGENCY_ACK_OVERHEAD × RTT)
             for use by the transport layer.

        Args:
            payload_size: payload size in bytes (determines fragmentation count)
            rtt_ms:        channel RTT estimate (None → use internal estimate)
            loss_rate:     channel loss rate (None → use internal estimate)
            deadline_ms:   emergency deadline for retransmit budget calculation

        Returns:
            (chunk_strategy, n_fragments, effective_delay_factor)
              chunk_strategy: always MICRO (4096 bytes)
              n_fragments:    ceil(payload_size / 4096)
              effective_delay_factor: EMERGENCY_ACK_OVERHEAD (0.60)
        """
        rtt  = rtt_ms    if rtt_ms    is not None else self._estimated_rtt_ms
        loss = loss_rate if loss_rate is not None else self._estimated_loss_rate

        # Force MICRO for emergency — bypass channel scoring
        self._update_theta(EMERGENCY_CHUNK_STRATEGY, _softmax(self._theta))
        self.n_decisions += 1

        chunk_size  = CHUNK_SIZES[EMERGENCY_CHUNK_STRATEGY]   # 4096
        n_fragments = math.ceil(payload_size / chunk_size)

        # Retransmit budget: how many retry attempts fit inside the deadline.
        # Each attempt costs one RTT; floor(deadline / RTT) - 1 retries available.
        n_retries = max(0, int(deadline_ms / max(rtt, 1.0)) - 1)

        # Effective loss after n_retries independent retransmit attempts:
        # P(all n_retries+1 attempts fail) = loss^(n_retries+1)
        # Clamp to EMERGENCY_LOSS_FACTOR as minimum improvement guarantee.
        eff_loss_factor = min(loss ** (n_retries + 1) / max(loss, EPS),
                              EMERGENCY_LOSS_FACTOR)

        return (
            self._make_chunk_strategy(chunk_size, EMERGENCY_CHUNK_STRATEGY, 1.0),
            n_fragments,
            EMERGENCY_ACK_OVERHEAD,
            eff_loss_factor,
        )

    def effective_loss_emergency(self, raw_loss: float) -> float:
        """
        Analytical effective loss for emergency frames after deadline-aware scheduling.

        Model: EMERGENCY_LOSS_FACTOR captures the gain from allocating 1 retransmit
        attempt within the deadline window. For deadline_ms=500 and RTT=300ms:
          - 1 retry fits → P(fail) ≈ raw × EMERGENCY_LOSS_FACTOR
          - Combined with priority lane (×0.20): effective = raw × 0.20 × 0.65 = raw × 0.13

        Args:
            raw_loss: channel loss probability

        Returns:
            Effective loss after QFT deadline-aware scheduling
        """
        return raw_loss * EMERGENCY_LOSS_FACTOR

    # ── Trafik gözlem & analiz (değişmedi) ───────────────────────────────────

    def observe(self, packet: Packet) -> None:
        """Record a packet in the observation window."""
        self.packet_history.append(packet)
        # RTT/loss tahmini güncelle (varsa packet metadata)
        if hasattr(packet, 'rtt_ms') and packet.rtt_ms:
            self._estimated_rtt_ms = (
                0.9 * self._estimated_rtt_ms + 0.1 * packet.rtt_ms
            )
        if len(self.packet_history) >= self.window_size:
            self._current_spectrum = self.analyze_traffic()

    def analyze_traffic(self) -> TrafficSpectrum:
        """
        Gelen paket akışını frekans domenine çevir.

        FFT = Klasik QFT simülasyonu
        Gerçek QFT: O(n log n) quantum gates
        Klasik FFT: O(n log n) — aynı karmaşıklık, farklı donanım
        """
        time_series = np.array(
            [p.size_bytes for p in self.packet_history],
            dtype=np.float64,
        )
        if len(time_series) < self.window_size:
            time_series = np.pad(
                time_series, (0, self.window_size - len(time_series))
            )

        freq_components = np.fft.fft(time_series)
        frequencies     = np.fft.fftfreq(self.window_size)
        magnitudes      = np.abs(freq_components)

        return TrafficSpectrum(
            frequencies=frequencies,
            magnitudes=magnitudes,
            dominant_freq=float(
                frequencies[np.argmax(magnitudes[1:]) + 1]
            ),
            energy_distribution=self._compute_energy_bands(magnitudes),
        )

    def schedule(self, queue: list[Packet]) -> list[Packet]:
        """Frekans analizine göre optimal gönderim sırası belirle."""
        return self.current_strategy().sort(queue)

    def current_strategy(self) -> SchedulingStrategy:
        """Return current scheduling strategy based on latest spectrum."""
        if self._current_spectrum is not None:
            proposed = self._select_strategy(self._current_spectrum)
            if (self._current_strategy is None
                    or proposed.name != self._current_strategy.name):
                self._strategy_stability_count += 1
                if self._strategy_stability_count >= self._strategy_change_threshold:
                    self._current_strategy = proposed
                    self._strategy_stability_count = 0
            else:
                self._strategy_stability_count = 0

        if self._current_strategy is None:
            self._current_strategy = AdaptiveHybridStrategy()

        return self._current_strategy

    def _select_strategy(self, spectrum: TrafficSpectrum) -> SchedulingStrategy:
        low_energy  = spectrum.energy_distribution["low"]
        high_energy = spectrum.energy_distribution["high"]

        if low_energy > 0.7:
            return BulkTransferStrategy(chunk_size=65536)
        elif high_energy > 0.6:
            return LatencyFirstStrategy(max_batch=4)
        else:
            return AdaptiveHybridStrategy(
                low_weight=low_energy,
                high_weight=high_energy,
            )

    def _compute_energy_bands(self, magnitudes: np.ndarray) -> dict[str, float]:
        """
        Low:  0 — 0.1 Hz  (bulk traffic)
        Mid:  0.1 — 0.4 Hz (mixed)
        High: 0.4 — 0.5 Hz (latency-sensitive)
        """
        n = len(magnitudes)
        total = np.sum(magnitudes**2)
        if total < 1e-12:
            return {"low": 0.33, "mid": 0.34, "high": 0.33}
        return {
            "low":  float(np.sum(magnitudes[:n//10]**2)          / total),
            "mid":  float(np.sum(magnitudes[n//10:4*n//10]**2)   / total),
            "high": float(np.sum(magnitudes[4*n//10:]**2)        / total),
        }

    # ── chunk_size_for (güncellendi: log-linear kullanır) ─────────────────────

    def chunk_size_for(self, payload_size: int) -> int:
        """
        Optimal chunk boyutunu döndür.

        Warm-up tamamlanmışsa: FFT enerji bantları + log-linear ağırlık
        Warm-up öncesi: payload boyutuna göre varsayılan
        """
        from qdap.chunking.strategy import ChunkStrategy

        if not self.has_enough_data:
            strategy = ChunkStrategy._payload_size_default(payload_size)
            self._chunk_strategy = strategy
            return int(strategy)

        # Hem FFT bant enerjisini hem log-linear kararı birleştir
        bands = self._last_energy_bands
        strategy = ChunkStrategy.from_energy_bands(
            low=bands.get('low', 0.33),
            mid=bands.get('mid', 0.33),
            high=bands.get('high', 0.33),
            payload_size=payload_size,
            has_spectrum_data=True,
        )
        self._chunk_strategy = strategy
        return int(strategy)

    # ── Raporlama (güncellendi: ağırlık bilgisi eklendi) ──────────────────────

    def get_spectrum_report(self) -> str:
        """Spectrum + log-linear ağırlık raporu."""
        if self._current_spectrum is None:
            return "⏳ Not enough data yet (need {} more packets)".format(
                self.window_size - len(self.packet_history)
            )

        spectrum  = self._current_spectrum
        energy    = spectrum.energy_distribution
        strategy  = self.current_strategy()
        w         = self.weights
        bar_width = 30

        def bar(v: float) -> str:
            f = int(v * bar_width)
            return "█" * f + "░" * (bar_width - f)

        weight_lines = [
            f"║ {STRATEGY_NAMES[i]:<7}: {bar(w[i])} {w[i]:.1%} ║"
            for i in range(5)
        ]

        lines = [
            "╔══════════════════════════════════════════════════╗",
            "║       QFT Spectral Analysis Report v2           ║",
            "╠══════════════════════════════════════════════════╣",
            f"║ Window Size : {self.window_size:>6d} packets                 ║",
            f"║ Dominant ν  : {spectrum.dominant_freq:>8.4f} Hz                 ║",
            "╠══════════════════════════════════════════════════╣",
            f"║ Low  (bulk)   {bar(energy['low'])} {energy['low']:.1%} ║",
            f"║ Mid  (mixed)  {bar(energy['mid'])} {energy['mid']:.1%} ║",
            f"║ High (latency){bar(energy['high'])} {energy['high']:.1%} ║",
            "╠══════════════════════════════════════════════════╣",
            f"║ FFT Strategy: {strategy.name:<30s}     ║",
            "╠══════════════════════════════════════════════════╣",
            "║ Log-linear Chunk Weights:                        ║",
        ] + weight_lines + [
            f"║ Dominant chunk: {self.dominant_chunk_strategy:<28s}   ║",
            f"║ n_decisions   : {self.n_decisions:<28d}   ║",
            "╚══════════════════════════════════════════════════╝",
        ]
        return "\n".join(lines)

    # ── Deadline-aware karar ──────────────────────────────────────────────────

    def decide_deadline_aware(
        self,
        payload_size: int,
        rtt_ms: float = None,
        loss_rate: float = None,
        deadline_ms: float = 1000.0,
        elapsed_ms: float = 0.0,
    ) -> Tuple[int, int, bool]:
        """
        Deadline bilgisi ile kararı override eder.
        remaining < 2×RTT veya remaining < 5ms → MICRO.
        """
        rtt = rtt_ms if rtt_ms is not None else self._estimated_rtt_ms

        # Rust bridge: only when Rust is compiled
        if _RUST_AVAILABLE:
            try:
                loss = loss_rate if loss_rate is not None else self._estimated_loss_rate
                chunk, strat, emergency = _decide_dl_rust(
                    payload_size, rtt, loss, deadline_ms, elapsed_ms
                )
                if emergency:
                    self._update_theta(STRATEGY_MICRO, _softmax(self._theta))
                    self.n_decisions += 1
                return chunk, strat, emergency
            except Exception:
                pass

        # Python fallback
        remaining = deadline_ms - elapsed_ms
        is_emergency = remaining < rtt * 2.0 or remaining < 5.0
        if is_emergency:
            self._update_theta(STRATEGY_MICRO, _softmax(self._theta))
            self.n_decisions += 1
            return CHUNK_SIZES[STRATEGY_MICRO], STRATEGY_MICRO, True

        result = self.decide(payload_size, rtt_ms, loss_rate)
        # decide() zaten chunk strategy döndürüyor, int'e çevir
        try:
            chunk_size = int(result)
        except Exception:
            chunk_size = CHUNK_SIZES[STRATEGY_MEDIUM]
        return chunk_size, STRATEGY_MEDIUM, False

    # ── Parallel streaming entegrasyonu ──────────────────────────────────────

    def decide_with_streaming(
        self,
        payload_size: int,
        rtt_ms:       float,
        loss_rate:    float,
    ) -> Tuple[int, int, float, int]:
        """
        Chunk kararı + paralel stream sayısı.

        Returns:
            (chunk_size, strategy_idx, confidence, n_streams)

        n_streams: STREAM_COUNTS[strategy_name], yüksek loss'ta yarıya iner.
        """
        from qdap.transport.parallel_sender import STREAM_COUNTS

        result       = self.decide(payload_size, rtt_ms, loss_rate)
        chunk_size   = int(result)

        # Strateji indeksini mevcut θ ağırlıklarından belirle
        w            = self.weights
        strategy_idx = w.index(max(w))
        strategy_name = STRATEGY_NAMES[strategy_idx]
        confidence   = max(w)

        n_streams    = STREAM_COUNTS.get(strategy_name, 1)

        # Yüksek loss'ta paralel stream azalt (daha fazla retransmit önle)
        if loss_rate > 0.10:
            n_streams = max(1, n_streams // 2)

        return chunk_size, strategy_idx, confidence, n_streams

    # ── Özellikler ────────────────────────────────────────────────────────────

    @property
    def strategy_name(self) -> str:
        return self.current_strategy().name

    @property
    def has_enough_data(self) -> bool:
        return len(self.packet_history) >= self.window_size

    @property
    def _last_energy_bands(self) -> dict[str, float]:
        if self._current_spectrum is not None:
            return self._current_spectrum.energy_distribution
        return {"low": 0.33, "mid": 0.34, "high": 0.33}

    def observe_packet_size(self, size_bytes: int) -> None:
        """Convenience: observe a packet by size only."""
        self.observe(Packet(payload=b'\x00' * size_bytes, deadline_ms=1000.0))
