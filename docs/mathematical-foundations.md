# Mathematical Foundations of QDAP

> Bu döküman QDAP'ın üç temel bileşeninin matematiksel temelini detaylı olarak açıklar.

---

## 1. Amplitude Encoding — Born Kuralı Analogu

### 1.1 Quantum State Normalization

Bir quantum state vektörü:

```
|ψ⟩ = Σ αᵢ|i⟩   where  Σ|αᵢ|² = 1
```

QDAP'ta her subframe `|i⟩` basis state'ine karşılık gelir. Amplitude `αᵢ`, subframe'in **öncelik ağırlığıdır**.

### 1.2 Öncelik Hesaplama

```
αᵢ_raw = f(deadline, size, type, history)
       = (1/deadline) × (1/log₂(size+2)) × type_weight × history_urgency

αᵢ = αᵢ_raw / ‖α_raw‖₂    (L2 normalization)
```

### 1.3 Born Kuralı → Scheduling

Born kuralı: `P(i) = |αᵢ|²` — ölçüm sonucu olasılığı.

QDAP analogu: `priority(i) = |αᵢ|²` — transmission sırası.

**Kanıtlanacak Lemma:** L2 normalleştirilmiş amplitude vektörü, subframe önceliklerinin convex combination'ıdır ve Σ|αᵢ|² = 1 koşulunu sağlar.

---

## 2. QFT → FFT Denkliği

### 2.1 Discrete Fourier Transform

```
X_k = Σ_{n=0}^{N-1} x_n · e^{-2πi·kn/N}
```

### 2.2 Quantum Fourier Transform

```
QFT|j⟩ = (1/√N) Σ_{k=0}^{N-1} e^{2πijk/N} |k⟩
```

**Denklik:** QFT, DFT'nin quantum circuit implementasyonudur. Matematiksel olarak aynı dönüşümü yapar.

### 2.3 Trafik Spektral Analizi

Paket büyüklüklerinin zaman serisi `x(t)`:
- **Düşük frekans** bileşeni → sürekli, büyük transferler
- **Yüksek frekans** bileşeni → kısa, anlık mesajlar

Enerji bandı dağılımı scheduling stratejisini belirler.

**Kanıtlanacak Teorem:** FFT tabanlı enerji bandı analizi, trafik tipini O(n log n)'de doğru sınıflandırır.

---

## 3. Ghost Session — Markov Kanal Modeli

### 3.1 Gilbert-Elliott Model

İki durumlu Markov chain: Good (G) ↔ Bad (B)

```
Transition matrix:
    P = [ p_gg  p_gb ]
        [ p_bg  p_bb ]

where p_gg + p_gb = 1 and p_bg + p_bb = 1
```

### 3.2 Kayıp Tahmin Mekanizması

Ghost State her pakette deterministik olarak güncellenir:
```
GS(t+1) = f(GS(t), observation(t))
```

Loss probability:
```
P(loss | age) = σ(2 × (age/E[RTT] - 2.5)) × (0.5 + p_bad)
```

where σ is the sigmoid function.

**Kanıtlanacak Önerme:** Ghost Session'ın false positive oranı, Markov chain parametre uyumu ile %90+ precision'a ulaşır.

---

## Referanslar

- arXiv:2311.10375 — Quantum Data Encoding
- Nielsen & Chuang — Quantum Computation and Information, Ch. 5 (QFT)
- Gilbert, E. N. — Capacity of a burst-noise channel (1960)
- Elliott, E. O. — Estimates of error rates for codes on burst-noise channels (1963)
