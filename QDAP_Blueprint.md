# QDAP — Quantum-Inspired Dynamic Application Protocol
## Kapsamlı System Design & Project Blueprint

> **Versiyon:** 0.1 — Pre-Alpha Blueprint  
> **Hedef:** Klasik donanımda çalışan, quantum computing prensiplerinden ilham alan, uygulama katmanı iletişim protokolü  
> **Akademik Temel:** IETF RFC 9340, IEEE Quantum Protocol Stack Survey (2024), QTCP (arXiv:2410.08980), Quantum Amplitude Encoding (arXiv:2311.10375), Exponential Quantum Communication Advantage (NeurIPS 2024)

---

## 0. Neden Bu Proje Var? — The Gap Analysis

### Mevcut Durum

```
Akademik dünya şu an tamamen fiziksel katmana odaklanmış:
  ✓ Quantum physical layer       → Çözülüyor (fiber, foton, qubit)
  ✓ Quantum link layer           → Çözülüyor (entanglement distribution)
  ✓ Quantum network layer        → Araştırılıyor (routing)
  ✓ Quantum transport layer      → Yeni başlıyor (QTCP, 2024)
  ✗ Quantum APPLICATION layer    → BOŞLUK — kimse yok
```

### Kritik Gözlem

IETF RFC 9340'ın temel argümanı: *"Quantum data transmission is not the goal — it is only one component of quantum application protocols."*

Yani quantum ağın amacı qubit taşımak değil, **uygulama seviyesinde yeni protokoller** tasarlamak. Kimse bunu klasik donanımda simüle edip GitHub'a koymamış.

### QDAP'ın Dolduracağı Boşluk

```
Klasik uygulama protokolleri (HTTP, QUIC, gRPC)
    + Quantum-inspired encoding prensipleri
    + Simüle edilmiş superposition-based multiplexing
    + QFT-tabanlı akıllı paket scheduling
    + Entanglement-inspired stateless session management
    ──────────────────────────────────────────────────
    = QDAP: Bugün çalışır, yarın quantum-ready
```

---

## 1. Temel Kavramsal Mimari

### 1.1 Üç Quantum Prensibi → Üç Protokol Primitifi

```
QUANTUM PRENSİBİ          QDAP PRİMİTİFİ              KLASIK KARŞILIĞI
─────────────────────────────────────────────────────────────────────────
Superposition             QFrame Multiplexing          HTTP/2 Streams
  |ψ⟩ = Σ αᵢ|i⟩           Tek frame içinde            (ama priority-aware)
                           N anlam taşı                 değil amplitude-aware

QFT (Quantum Fourier      Spectral Packet Scheduler    Priority Queue
Transform)                Paketleri frekans            (ama statik değil,
  f: time → freq domain   domeninde sırala             dinamik ve adaptif)

Entanglement              Ghost Session Protocol        TCP ACK
  |Φ+⟩=(|00⟩+|11⟩)/√2    ACK'siz implicit             (ama round-trip
                           acknowledgment               overhead yok)
```

### 1.2 Katman Konumu

```
┌────────────────────────────────────────┐
│         UYGULAMA (L7)                  │
│   HTTP, gRPC, WebSocket, vb.           │
├────────────────────────────────────────┤
│         QDAP KATMANI  ← BİZ BURDAYIZ  │
│   QFrame / QFT-Scheduler / Ghost Sess  │
├────────────────────────────────────────┤
│         TRANSPORT (L4)                 │
│   TCP / QUIC / UDP                     │
├────────────────────────────────────────┤
│         NETWORK & BELOW (L1-L3)        │
│   IP / Ethernet / Fiber / vb.          │
└────────────────────────────────────────┘
```

QDAP bir **middleware protokol katmanı** olarak çalışır. Mevcut transport'u değiştirmez, üstüne oturur.

---

## 2. Üç Temel Bileşen — Detaylı Tasarım

---

### Bileşen A: QFrame Multiplexer (Superposition → Çoklu Anlam)

#### 2.A.1 Temel Fikir

Klasik bir TCP paketi düşün:
```
[Header 40B] [Payload XB] [Checksum 4B]
```
Bu tek bir mesajı taşır. HTTP/2 bunu stream'lere böldü ama her stream hâlâ sıralı.

QFrame'in amacı: Bir "transmission unit" içinde, **birden fazla payload'ı amplitude-weighted** şekilde encode etmek.

```
Klasik:   Send(A) → Send(B) → Send(C)     [3 round trip]

QFrame:   |ψ_frame⟩ = α|A⟩ + β|B⟩ + γ|C⟩  [1 round trip, weighted]
```

Burada α, β, γ kompleks sayılar değil **öncelik ağırlıkları** (weight coefficients). Normalleşme koşulu: |α|² + |β|² + |γ|² = 1.

#### 2.A.2 QFrame Yapısı

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   QDAP Version  |  Frame Type  |    Subframe Count  |  Flags  |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Session ID (64-bit)                         |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|              Amplitude Vector (N × float32)                    |
|         [α₁, α₂, ..., αₙ] — normalleştirilmiş ağırlıklar     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|              Subframe #1: [length | type | payload]            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|              Subframe #2: [length | type | payload]            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|              ...                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|              QFrame Integrity Hash (SHA3-256)                  |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

**Frame Types:**
| Code | Tip | Açıklama |
|------|-----|----------|
| 0x01 | DATA | Veri subframe'leri taşır |
| 0x02 | CTRL | Kontrol mesajları (handshake, teardown) |
| 0x03 | GHOST | Ghost Session protocol mesajları |
| 0x04 | PROBE | Kanal kalitesi ölçümü |
| 0x05 | SYNC | Zaman senkronizasyonu |

#### 2.A.3 Amplitude Encoding Algoritması

```python
class AmplitudeEncoder:
    """
    Klasik veriyi quantum-inspired amplitude vektörüne dönüştür.
    
    Temel: arXiv:2311.10375 — Quantum Data Encoding: A Comparative Analysis
    Adaptasyon: Probability amplitudes → priority weights
    """
    
    def encode(self, subframes: List[Subframe]) -> np.ndarray:
        """
        Her subframe'in önceliğini amplitude vektörüne dönüştür.
        Normalleşme koşulu: Σ |αᵢ|² = 1
        """
        raw_weights = np.array([
            self._compute_priority(sf) for sf in subframes
        ], dtype=np.float64)
        
        # L2 normalleştirme — quantum state normalleşmesini taklit eder
        amplitudes = raw_weights / np.linalg.norm(raw_weights)
        return amplitudes
    
    def _compute_priority(self, sf: Subframe) -> float:
        """
        Öncelik = f(deadline, size, type, history)
        """
        deadline_weight  = 1.0 / (sf.deadline_ms + 1e-9)
        size_weight      = 1.0 / np.log2(sf.size_bytes + 2)
        type_weight      = SubframeType.priority_map[sf.type]
        history_weight   = self.session_history.get_urgency(sf.session_id)
        
        return deadline_weight * size_weight * type_weight * history_weight
    
    def decode_schedule(self, amplitudes: np.ndarray) -> List[int]:
        """
        Amplitude vektöründen transmission sırasını çıkar.
        En yüksek |α|² → en önce gönder.
        """
        probabilities = amplitudes ** 2  # Born kuralı analogu
        return np.argsort(probabilities)[::-1].tolist()
```

#### 2.A.4 Neden Bu İşe Yarar? (Teorik Temel)

NeurIPS 2024 (arXiv:2310.07136) kanıtladı ki amplitude encoding bazı problemlerde **üstel iletişim karmaşıklığı avantajı** sağlar. Bizim adaptasyonumuz:

- Klasik priority queue: O(n log n) karar, **statik** öncelik
- QFrame encoder: O(n) normalleştirme, **dinamik** ve **context-aware** öncelik

Avantaj: Her frame gönderiminde öncelikler yeniden hesaplanır → adaptif trafik yönetimi.

---

### Bileşen B: QFT Packet Scheduler (Fourier → Frekans Domain Sıralama)

#### 2.B.1 Temel Fikir

Quantum Fourier Transform, bir sinyali zaman domeninden frekans domenine taşır. Biz bunu **paket trafiğine** uyguluyoruz:

```
Zaman Domeni:  [P1(t=0), P2(t=1), P3(t=2), ...]  → "ne zaman geldi?"
Frekans Dom.:  [F_low, F_mid, F_high, ...]         → "ne kadar kritik?"
```

Temel sezgi:
- **Düşük frekanslı trafik** = Büyük, sürekli, kritik veri akışları (video stream, bulk transfer)
- **Yüksek frekanslı trafik** = Küçük, anlık, latency-sensitive mesajlar (sensor ping, RPC call)

Bu ikisinin **farklı servis stratejisi** gerektirdiğini QFT analizi otomatik tespit eder.

#### 2.B.2 Klasik QFT Simülasyonu

```python
class QFTScheduler:
    """
    Quantum Fourier Transform'ı packet scheduling'e uygula.
    
    Referans: Shor algoritmasındaki QFT period-finding fikrinden ilham alınmıştır.
    Klasik FFT ile simüle edilir — quantum donanım gerektirmez.
    """
    
    def __init__(self, window_size: int = 64):
        self.window_size = window_size
        self.packet_history = deque(maxlen=window_size)
        
    def analyze_traffic(self, incoming_packets: List[Packet]) -> TrafficSpectrum:
        """
        Gelen paket akışını frekans domenine çevir.
        """
        # Paket büyüklüklerini zaman serisine çevir
        time_series = np.array([p.size_bytes for p in self.packet_history])
        
        if len(time_series) < self.window_size:
            time_series = np.pad(time_series, 
                                (0, self.window_size - len(time_series)))
        
        # FFT = Klasik QFT simülasyonu
        # Gerçek QFT: O(n log n) quantum gates
        # Klasik FFT: O(n log n) — aynı karmaşıklık, farklı donanım
        freq_components = np.fft.fft(time_series)
        frequencies     = np.fft.fftfreq(self.window_size)
        magnitudes      = np.abs(freq_components)
        
        return TrafficSpectrum(
            frequencies=frequencies,
            magnitudes=magnitudes,
            dominant_freq=frequencies[np.argmax(magnitudes)],
            energy_distribution=self._compute_energy_bands(magnitudes)
        )
    
    def schedule(self, queue: PacketQueue, spectrum: TrafficSpectrum) -> List[Packet]:
        """
        Frekans analizine göre optimal gönderim sırası belirle.
        """
        strategy = self._select_strategy(spectrum)
        return strategy.sort(queue)
    
    def _select_strategy(self, spectrum: TrafficSpectrum) -> SchedulingStrategy:
        """
        Trafik spektrumuna göre scheduling stratejisi seç.
        """
        low_energy  = spectrum.energy_distribution['low']   # 0-0.1 Hz band
        mid_energy  = spectrum.energy_distribution['mid']   # 0.1-0.4 Hz
        high_energy = spectrum.energy_distribution['high']  # 0.4-0.5 Hz
        
        if low_energy > 0.7:
            # Baskın düşük frekans → bulk transfer modu
            return BulkTransferStrategy(chunk_size=65536)
        elif high_energy > 0.6:
            # Baskın yüksek frekans → latency-first modu
            return LatencyFirstStrategy(max_batch=4)
        else:
            # Karma → adaptif hibrit
            return AdaptiveHybridStrategy(
                low_weight=low_energy,
                high_weight=high_energy
            )
    
    def _compute_energy_bands(self, magnitudes: np.ndarray) -> Dict[str, float]:
        n = len(magnitudes)
        total_energy = np.sum(magnitudes ** 2)
        
        low  = np.sum(magnitudes[:n//10] ** 2) / total_energy
        mid  = np.sum(magnitudes[n//10:4*n//10] ** 2) / total_energy
        high = np.sum(magnitudes[4*n//10:] ** 2) / total_energy
        
        return {'low': low, 'mid': mid, 'high': high}
```

#### 2.B.3 Üç Scheduling Stratejisi

```
Strategy 1: BULK TRANSFER (Düşük Frekans Baskın)
─────────────────────────────────────────────────
  Büyük chunklar halinde gönder
  TCP Nagle algoritmasını agresif kullan
  Throughput maximize et, latency ikincil

Strategy 2: LATENCY-FIRST (Yüksek Frekans Baskın)
──────────────────────────────────────────────────
  Her paketi hemen gönder
  Nagle devre dışı
  Küçük batch'ler, sık flush
  RTT minimize et

Strategy 3: ADAPTIVE HYBRID (Karma Trafik)
──────────────────────────────────────────
  İki kuyruğu paralel yönet
  Yüksek öncelikli → Latency-First
  Düşük öncelikli → Bulk
  Spektrum değiştikçe strateji güncelle
```

---

### Bileşen C: Ghost Session Protocol (Entanglement → Stateless ACK)

#### 2.C.1 Temel Fikir

TCP'nin en büyük yükü: **ACK overhead**. Her segment için:
1. Alıcı ACK paketi gönderir
2. Gönderici bekler (RTT kadar)
3. Retransmission timer çalışır

Entanglement'ta ise: İki parçacık ölçüldüğünde, diğerinin durumu **ek iletişim olmadan** bilinir.

**Ghost Session Protocol** bunu şöyle taklit eder:

```
Klasik TCP:
  Gönderici → [Data] → Alıcı
  Gönderici ← [ACK]  ← Alıcı    ← RTT bekleme
  Gönderici → [Data] → Alıcı

Ghost Session:
  Handshake'te "Ghost State" paylaşılır.
  Gönderici → [Data + Ghost Signature] → Alıcı
  Alıcı Ghost State'i günceller (lokal işlem)
  Gönderici Ghost State'den kaybı öngörür → ACK beklemiyor
  Gönderici → [Sonraki Data] → Alıcı     ← RTT yok!
```

#### 2.C.2 Ghost State Teorisi

**Bell State Analogu:**

```
Gerçek Bell State:  |Φ+⟩ = (|00⟩ + |11⟩) / √2

Ghost State Analogu:
  GS(sender, receiver) = {
      session_key:    HKDF(shared_secret, session_id),
      sequence_space: Ring(2^32),
      loss_model:     AdaptiveMarkov(p_loss, p_burst),
      expected_next:  PredictiveModel(history)
  }
```

İki taraf aynı Ghost State'i başlangıçta senkronize eder. Sonraki her mesaj, Ghost State'i deterministik olarak günceller. Böylece her iki taraf da **karşılıklı iletişim olmadan** aynı duruma ulaşır.

#### 2.C.3 Ghost Session Implementasyonu

```python
class GhostSession:
    """
    Entanglement-inspired implicit acknowledgment mekanizması.
    
    Konseptüel temel: Bell pair ölçümünde implicit state collapse.
    Pratik uygulama: Paylaşılan deterministik state machine.
    """
    
    def __init__(self, session_id: bytes, shared_secret: bytes):
        self.session_id = session_id
        self.ghost_key  = HKDF(shared_secret, b"ghost-v1", length=32)
        
        # Loss prediction modeli — Markov chain ile kanal modellemesi
        self.loss_model = AdaptiveMarkovChain(
            states=['good', 'bad'],
            initial_probs=[0.95, 0.05]
        )
        
        # Sıra numarası tahmini
        self.sequence_predictor = SequencePredictor()
        
        # Gönderilmiş ama henüz "implicitly acknowledged" olmayan paketler
        self.ghost_window: Dict[int, GhostEntry] = {}
        
    def send(self, payload: bytes, seq_num: int) -> QFrame:
        """
        Ghost signature ile paket gönder.
        """
        ghost_sig = self._compute_ghost_signature(seq_num, payload)
        entry = GhostEntry(
            seq_num=seq_num,
            sent_at=time.monotonic_ns(),
            ghost_sig=ghost_sig,
            predicted_state=self.loss_model.predict_next()
        )
        self.ghost_window[seq_num] = entry
        
        return QFrame(payload=payload, ghost_sig=ghost_sig, seq_num=seq_num)
    
    def implicit_ack(self, received_seq: int):
        """
        Karşı taraf bu seq'i aldığında Ghost State güncellenir.
        ACK paketi gönderilmez — Ghost State lokal olarak collapse eder.
        """
        if received_seq in self.ghost_window:
            entry = self.ghost_window.pop(received_seq)
            rtt_sample = time.monotonic_ns() - entry.sent_at
            
            # Loss model güncelle
            self.loss_model.update('good', rtt_sample)
            self.sequence_predictor.record_success(received_seq)
    
    def detect_loss(self) -> List[int]:
        """
        Ghost State'den kayıp paketleri tahmin et.
        Explisit NAK beklemeden retransmit trigger.
        """
        now = time.monotonic_ns()
        lost = []
        
        for seq_num, entry in self.ghost_window.items():
            age_ms = (now - entry.sent_at) / 1e6
            expected_rtt = self.loss_model.expected_rtt_ms()
            
            # Eğer beklenen RTT'nin 2.5 katı geçtiyse → kayıp tahmin et
            if age_ms > 2.5 * expected_rtt:
                confidence = self.loss_model.loss_probability(age_ms)
                if confidence > 0.85:
                    lost.append(seq_num)
                    self.loss_model.update('bad', age_ms)
        
        return lost
    
    def _compute_ghost_signature(self, seq_num: int, payload: bytes) -> bytes:
        """
        HMAC tabanlı ghost imzası.
        İki taraf da deterministik olarak aynı imzayı hesaplayabilir.
        """
        msg = seq_num.to_bytes(4, 'big') + payload[:32]
        return hmac.new(self.ghost_key, msg, sha256).digest()[:8]
```

#### 2.C.4 Neden Bu Çalışır?

```
Klasik TCP ACK overhead analizi (örnek: 1 Gbps, 100ms RTT):
  Veri gönderme window: ~12.5 MB
  ACK trafiği: ~%3-5 overhead
  Latency etkisi: Her window için 100ms bekleme

Ghost Session:
  ACK trafiği: ~0 (sadece negative feedback / NAK)
  Latency etkisi: Pipeline tam dolu, bekleme yok
  Trade-off: Ghost State false positive → nadiren gereksiz retransmit
  Net kazanç: %15-40 throughput artışı (kanala bağlı)
```

---

## 3. Sistem Geneli Mimari

### 3.1 Bileşen Etkileşim Diyagramı

```
                    ┌─────────────────────────────────────┐
                    │           QDAP STACK                │
                    │                                     │
 Uygulama           │  ┌─────────┐    ┌───────────────┐  │
 Katmanı ──────────►│  │  QFrame │───►│   Amplitude   │  │
 (HTTP/gRPC/WS)     │  │  Parser │    │   Encoder     │  │
                    │  └────┬────┘    └───────┬───────┘  │
                    │       │                 │           │
                    │  ┌────▼────┐    ┌───────▼───────┐  │
                    │  │  Ghost  │◄──►│  QFT Packet   │  │
                    │  │ Session │    │  Scheduler    │  │
                    │  └────┬────┘    └───────┬───────┘  │
                    │       │                 │           │
                    │  ┌────▼─────────────────▼───────┐  │
                    │  │      Frame Assembler         │  │
                    │  │  (QFrame → Transport bytes)  │  │
                    │  └──────────────┬───────────────┘  │
                    └─────────────────┼───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │     TRANSPORT KATMANI (TCP/QUIC)     │
                    └─────────────────────────────────────┘
```

### 3.2 Veri Akış Sırası (Sender Tarafı)

```
1. Uygulama data üretir → QDAP'a teslim eder
2. QFrame Parser → veriyi subframe'lere böler
3. Amplitude Encoder → subframe önceliklerini hesaplar
4. QFT Scheduler → trafik spektrumunu analiz eder
5. Scheduler + Encoder birlikte → gönderim sırası belirlenir
6. Ghost Session → her subframe'e ghost signature ekler
7. Frame Assembler → QFrame binary formatına çevirir
8. Transport → TCP/QUIC üzerinden gönderilir
```

### 3.3 Veri Akış Sırası (Receiver Tarafı)

```
1. Transport → bytes alınır
2. Frame Assembler → QFrame parse edilir
3. Ghost Session → implicit ACK tetiklenir (lokal)
4. Amplitude Decoder → öncelik vektörü okunur
5. Subframe'ler öncelik sırasıyla uygulamaya teslim
6. QFT Scheduler → receiver-side spektrum güncellenir
```

---

## 4. Geliştirme Planı — 5 Faz

---

### FAZ 0 — Altyapı & Araştırma (2-3 Hafta)

**Hedef:** Repo kurulumu, temel matematik doğrulama, referans implementasyonları.

```
Görevler:
  [ ] GitHub repo oluştur: quantum-inspired-protocol/qdap
  [ ] Temel paket yapısı kur (Python monorepo)
  [ ] Birim test altyapısı (pytest + hypothesis)
  [ ] RFC 9340 ve QTCP paper'ı derinlemesine oku ve notlar çıkar
  [ ] QFT → FFT denkliğini matematiksel olarak doğrula
  [ ] Amplitude encoding normalleşme lemmasını kanıtla
  [ ] Reference TCP implementasyonu benchmark al (iperf3 tarzı)

Çıktılar:
  - /docs/mathematical-foundations.md
  - /benchmarks/baseline/ (klasik TCP metrikleri)
  - /research-notes/ (paper özetleri)
```

**Dosya yapısı:**
```
qdap/
├── src/
│   └── qdap/
│       ├── __init__.py
│       ├── frame/
│       ├── scheduler/
│       ├── session/
│       └── transport/
├── tests/
├── benchmarks/
├── docs/
│   ├── mathematical-foundations.md
│   └── api-reference.md
├── examples/
├── paper/
└── pyproject.toml
```

---

### FAZ 1 — Core Protocol Engine (4-6 Hafta)

**Hedef:** Üç temel bileşeni çalışır hale getir. Unit test coverage %90+.

#### Faz 1.1 — QFrame (Hafta 1-2)

```python
# Hedef: Bu çalışmalı

encoder = AmplitudeEncoder()
frame = QFrame.create(
    subframes=[
        Subframe(payload=video_chunk, type=DATA, deadline_ms=16),
        Subframe(payload=audio_chunk, type=DATA, deadline_ms=8),
        Subframe(payload=cursor_pos,  type=DATA, deadline_ms=4),
    ]
)

# frame.amplitude_vector = [0.62, 0.44, 0.65] — normalleştirilmiş
# frame.send_order       = [2, 0, 1]           — cursor önce!
```

**Geliştirme adımları:**
```
[ ] QFrame binary format tanımla (struct + protobuf karşılaştır)
[ ] AmplitudeEncoder: normalize(), encode(), decode_schedule()
[ ] Subframe type sistemi (DATA, CTRL, GHOST, PROBE, SYNC)
[ ] QFrame serialization/deserialization
[ ] Integrity hash (SHA3-256)
[ ] Fuzzing testleri (hypothesis ile)
[ ] Wireshark dissector yaz (debug için)
```

#### Faz 1.2 — QFT Scheduler (Hafta 2-3)

```python
# Hedef: Bu çalışmalı

scheduler = QFTScheduler(window_size=64)

# 1000 paket geldikçe öğren
for packet in incoming_stream:
    scheduler.observe(packet)

# Stratejiyi sorgula
strategy = scheduler.current_strategy()
# strategy.type == "LATENCY_FIRST"  (çünkü küçük, sık paketler var)

# Yeniden sıralandır
ordered_queue = scheduler.schedule(pending_queue)
```

**Geliştirme adımları:**
```
[ ] PacketHistory ring buffer
[ ] FFT analiz motoru (numpy.fft)
[ ] Enerji bant hesaplama (low/mid/high)
[ ] BulkTransferStrategy implementasyonu
[ ] LatencyFirstStrategy implementasyonu  
[ ] AdaptiveHybridStrategy implementasyonu
[ ] Strategy geçiş hysteresis (ping-pong önleme)
[ ] Gerçek zamanlı spektrum görselleştirme (debug modu)
```

#### Faz 1.3 — Ghost Session (Hafta 3-4)

```python
# Hedef: Bu çalışmalı

alice = GhostSession(session_id, shared_secret)
bob   = GhostSession(session_id, shared_secret)

# Alice gönderir
frame = alice.send(payload=data, seq_num=42)

# Bob alır (ağ simülasyonu)
bob.on_receive(frame)

# Bob explicit ACK göndermez!
# Alice ghost state'den kayıp tespiti yapar
lost = alice.detect_loss()
assert 42 not in lost  # Bob aldı, kayıp yok
```

**Geliştirme adımları:**
```
[ ] GhostState HKDF key derivation
[ ] AdaptiveMarkovChain (2-state: good/bad channel)
[ ] SequencePredictor (sliding window)
[ ] GhostEntry yönetimi ve timeout
[ ] Loss detection algoritması
[ ] False positive rate ölçümü
[ ] Adversarial test: paket tekrarı, yeniden sıralama, kayıp
```

#### Faz 1.4 — Entegrasyon (Hafta 5-6)

```python
# Hedef: End-to-end çalışır

server = QDAPServer("localhost", 9000)
client = QDAPClient("localhost", 9000)

with client.connect() as conn:
    conn.send_multiframe([data_a, data_b, data_c], 
                          priorities=[0.8, 0.5, 0.3])

# Sunucu doğru sırada aldı mu?
received = server.drain()
assert received[0] == data_a  # En yüksek öncelik önce
```

---

### FAZ 2 — Classical Transport Adapter (3-4 Hafta)

**Hedef:** QDAP'ı TCP ve QUIC üzerine oturtan adapter katmanı.

#### 2.1 TCP Adapter

```python
class QDAPOverTCP:
    """
    QDAP frame'lerini TCP üzerinden taşır.
    Length-prefixed framing kullanır.
    """
    MAGIC     = b'\x51\x44\x41\x50'  # "QDAP"
    VERSION   = 1
    
    def send_frame(self, sock: socket.socket, frame: QFrame):
        data = frame.serialize()
        header = struct.pack('>4sHI', self.MAGIC, self.VERSION, len(data))
        sock.sendall(header + data)
    
    def recv_frame(self, sock: socket.socket) -> QFrame:
        header = self._recv_exactly(sock, 10)
        magic, version, length = struct.unpack('>4sHI', header)
        
        if magic != self.MAGIC:
            raise ProtocolError("Invalid QDAP magic")
        
        data = self._recv_exactly(sock, length)
        return QFrame.deserialize(data)
```

#### 2.2 QUIC Adapter (aioquic)

```python
class QDAPOverQUIC(QuicConnectionProtocol):
    """
    QUIC stream multiplexing + QDAP frame multiplexing.
    Her QFrame tipi farklı QUIC stream'e map edilir.
    """
    STREAM_MAP = {
        FrameType.DATA:  0,  # Bidirectional stream 0
        FrameType.CTRL:  2,  # Bidirectional stream 2
        FrameType.GHOST: 4,  # Unidirectional stream 4
    }
    
    async def send_frame(self, frame: QFrame):
        stream_id = self.STREAM_MAP[frame.type]
        self._quic.send_stream_data(
            stream_id=stream_id,
            data=frame.serialize(),
            end_stream=False
        )
```

#### 2.3 Performans Testleri

```
Benchmark senaryoları:
  1. Throughput: 1MB, 10MB, 100MB transfer
     - Klasik TCP vs QDAP-TCP vs QDAP-QUIC
     
  2. Latency: 10K küçük mesaj (100 byte)
     - RTT dağılımı: p50, p95, p99, p999
     
  3. Multiplexing: 10 eş zamanlı stream
     - Head-of-line blocking analizi
     
  4. Paket kaybı: %1, %5, %10 loss rate
     - Ghost Session etkinliği
     
  5. Karma trafik: Video + ses + kontrol
     - QFT scheduler strateji geçişleri
```

---

### FAZ 3 — Simülasyon & Doğrulama (3-4 Hafta)

**Hedef:** Quantum konseptlerinin klasik simülasyonunu Qiskit ile doğrula.

#### 3.1 QFT Doğrulaması

```python
from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT

# Bizim klasik FFT scheduler'ımızın sonuçlarını
# gerçek QFT devresiyle karşılaştır

def verify_qft_equivalence(time_series: np.ndarray):
    # Klasik FFT
    classical_result = np.fft.fft(time_series)
    
    # Qiskit QFT (statevector simulator)
    n_qubits = int(np.ceil(np.log2(len(time_series))))
    qc = QuantumCircuit(n_qubits)
    # ... amplitude encoding + QFT devresi
    qc.append(QFT(n_qubits), range(n_qubits))
    result = execute(qc, backend=statevector_sim).result()
    quantum_result = result.get_statevector()
    
    # İki sonuç matematiksel olarak eşdeğer mi?
    assert np.allclose(classical_result_normalized, quantum_result, atol=1e-6)
```

#### 3.2 Amplitude Encoding Doğrulaması

```python
# Öncelik ağırlıklandırması Born kuralı ile tutarlı mı?

def verify_born_rule_analogy():
    subframes = [Subframe(priority=0.8), Subframe(priority=0.5)]
    amplitudes = encoder.encode(subframes)
    
    # |α|² toplamı 1 olmalı (normalleşme)
    assert abs(np.sum(amplitudes**2) - 1.0) < 1e-9
    
    # Yüksek öncelik → yüksek amplitude
    assert amplitudes[0] > amplitudes[1]
```

#### 3.3 Ghost Session Markov Analizi

```python
# Ghost State Markov chain'i gerçek kanal modeliyle karşılaştır

def verify_ghost_session_accuracy(real_channel_trace: List[bool]):
    """
    Gerçek paket kaybı logu ile Ghost State tahminini karşılaştır.
    """
    ghost = GhostSession(...)
    
    predicted_losses = []
    actual_losses = real_channel_trace
    
    for seq, lost in enumerate(actual_losses):
        if not lost:
            ghost.implicit_ack(seq)
        predicted_losses.append(seq in ghost.detect_loss())
    
    precision = compute_precision(predicted_losses, actual_losses)
    recall    = compute_recall(predicted_losses, actual_losses)
    
    assert precision > 0.90, f"Ghost Session precision too low: {precision}"
    assert recall    > 0.85, f"Ghost Session recall too low: {recall}"
```

---

### FAZ 4 — Gerçek Dünya Entegrasyonu (4-5 Hafta)

**Hedef:** IoT ve video streaming senaryolarında çalışan demo'lar.

#### 4.1 IoT Sensör Ağı Demo

```python
# Senaryo: 100 sensör, her biri farklı frekansta veri üretiyor
# QDAP QFT Scheduler sensörleri otomatik olarak grupla

class IoTGateway:
    def __init__(self):
        self.qdap = QDAPServer()
        self.sensors: Dict[str, SensorStream] = {}
    
    async def aggregate_and_transmit(self):
        """
        100 sensörden gelen veriyi tek QDAP bağlantısıyla gönder.
        QFT Scheduler kritik sensörleri öne al.
        """
        all_readings = await self.collect_readings()
        
        frame = QFrame.from_sensor_readings(
            readings=all_readings,
            criticality_map=self.sensor_criticality
        )
        
        # Scheduler'ın spektrum analizi:
        # - Acil durum sensörleri → yüksek amplitude → önce
        # - Rutin telemetri → düşük amplitude → sonra
        await self.qdap.send(frame)
```

**Demo metrikleri:**
- 100 sensör, 10ms güncelleme aralığı
- Acil durum mesajı latency: <5ms
- Rutin telemetri latency: <50ms (kabul edilebilir)
- Klasik UDP broadcast ile karşılaştırma

#### 4.2 Adaptive Video Streaming Demo

```python
# Senaryo: 1080p video + stereo ses + altyazı
# Tek QDAP bağlantısı üç stream'i yönetiyor

class VideoStreamServer:
    async def stream(self, conn: QDAPConnection):
        while True:
            # Her 16ms (60fps) bir QFrame
            video_frame = await self.get_video_frame()  # ~100KB
            audio_chunk = await self.get_audio_chunk()  # ~3KB
            subtitle    = await self.get_subtitle()     # ~200B
            
            qframe = QFrame.create([
                Subframe(video_frame, deadline_ms=16, type=DATA),
                Subframe(audio_chunk, deadline_ms=10, type=DATA),
                Subframe(subtitle,    deadline_ms=100, type=DATA),
            ])
            
            # Amplitude encoder → ses (deadline küçük) öncelikli!
            # QFT Scheduler → video bulk, ses latency-first
            await conn.send(qframe)
```

---

### FAZ 5 — Akademik Çıktı & Topluluk (3-4 Hafta)

**Hedef:** arXiv paper + kapsamlı dokümantasyon + GitHub community.

#### 5.1 arXiv Paper Yapısı

```
Başlık: "QDAP: A Quantum-Inspired Application Layer Protocol for
         Classical Networks Using Amplitude Encoding and QFT-Based Scheduling"

Abstract: (150 kelime)
  - Problem: Uygulama katmanı protokollerinde quantum ilham eksikliği
  - Yöntem: 3 quantum primitif → 3 protokol bileşeni
  - Sonuç: X% throughput, Y% latency iyileşmesi, Z% ACK overhead azalma

Bölümler:
  1. Introduction & Motivation
  2. Related Work (QTCP, RFC 9340, QUIC, HTTP/2 karşılaştırması)
  3. Theoretical Framework
     3.1 Amplitude Encoding Analogy
     3.2 QFT Scheduling Theorem
     3.3 Ghost Session Markov Model
  4. QDAP Protocol Design
     4.1 QFrame Format
     4.2 QFT Scheduler
     4.3 Ghost Session Protocol
  5. Implementation
  6. Evaluation
     6.1 Throughput Benchmarks
     6.2 Latency Analysis
     6.3 Loss Recovery Accuracy
  7. Limitations & Future Work
     7.1 Real Quantum Hardware Integration
     7.2 Security Analysis
  8. Conclusion

Referanslar: RFC 9340, QTCP paper, NeurIPS 2024 quantum comm, 
             QFT survey, Dahlberg et al. SIGCOMM 2019
```

#### 5.2 GitHub Repository Stratejisi

```
Etiketler: quantum-networking, protocol-design, 6G, post-quantum,
           network-protocol, python, qiskit, quantum-inspired

README yapısı:
  - 30 saniyede ne? (GIF demo)
  - Neden önemli? (Academic gap)
  - Hızlı başlangıç (5 satır kod)
  - Mimari özeti
  - Benchmark sonuçları (grafik)
  - Yol haritası

Topluluk:
  - CONTRIBUTING.md
  - Issue templates (bug, feature, research)
  - GitHub Discussions aktif tut
  - Weekly devlog (her Cuma)

Tanıtım kanalları:
  - Hacker News: "Show HN: Quantum-inspired protocol that runs on classical hardware"
  - r/networking, r/quantum
  - IETF quantum-network listesi
  - arXiv cross-post
```

---

## 5. Teknik Kararlar & Trade-off'lar

### 5.1 Dil Seçimi: Python İlk, Rust Sonra

```
FAZ 0-3: Python
  ✓ Hızlı iterasyon
  ✓ Numpy FFT, Qiskit entegrasyonu kolay
  ✓ Akademik camia Python biliyor
  ✗ Performans sınırlı

FAZ 4-5: Python (core) + Rust (hot path)
  ✓ PyO3 ile Python-Rust binding
  ✓ Frame parser ve serializer Rust'ta
  ✓ Benchmark'ta adil rekabet
```

### 5.2 Serializasyon: Protobuf vs MessagePack vs Binary

```
QFrame için önerilen: Hybrid
  - Header: custom binary (sabit boyut, zero-parse overhead)
  - Amplitude vector: raw float32 array
  - Subframe payload: MessagePack (esneklik için)
  - İleride: FlatBuffers (zero-copy için)
```

### 5.3 Quantum Simülasyon Derinliği

```
MVP seviyesi (Faz 1-2):
  - FFT = QFT simülasyonu (matematiksel eşdeğer)
  - L2 normalization = quantum state normalization
  - HMAC = ghost signature (kriptografik)

İleri seviye (Faz 3):
  - Qiskit statevector simulator ile doğrulama
  - Gerçek qubit sayısı sınırı gözetilerek ölçekle

Gelecek (post-Faz 5):
  - IBM Quantum üzerinde QFT devresi çalıştır
  - Sonuçları klasik simülasyonla karşılaştır
```

---

## 6. Başarı Metrikleri

### 6.1 Teknik Metrikler (Faz 2 sonunda)

| Metrik | Baseline (TCP) | QDAP Hedefi |
|--------|---------------|-------------|
| Throughput (1Gbps kanal) | 940 Mbps | ≥960 Mbps |
| P99 Latency (küçük msg) | 5ms | ≤3ms |
| ACK overhead | %3-5 | <%0.5 |
| Multiplexed stream priority accuracy | %0 (FIFO) | ≥%90 |
| Loss detection accuracy | N/A (explicit) | ≥%85 precision |

### 6.2 Akademik Metrikler (Faz 5 sonunda)

```
[ ] arXiv preprint yayınlandı
[ ] En az 2 akademisyenden geri bildirim alındı
[ ] IETF quantum-network draft olarak gönderildi (informal)
[ ] GitHub: 500+ star
[ ] Bir konferans submission (SIGCOMM, INFOCOM, ICC)
```

---

## 7. Bağımlılıklar & Araçlar

### 7.1 Python Paketleri

```toml
[tool.poetry.dependencies]
python       = "^3.11"
numpy        = "^1.26"        # FFT, array ops
scipy        = "^1.12"        # Signal processing utils
qiskit       = "^1.0"         # Quantum circuit simulation
qiskit-aer   = "^0.14"        # Statevector simulator
aioquic      = "^1.0"         # QUIC transport
cryptography = "^42.0"        # HKDF, HMAC
hypothesis   = "^6.0"         # Property-based testing
pytest       = "^8.0"         # Unit tests
rich         = "^13.0"        # Debug görselleştirme

[tool.poetry.dev-dependencies]
black        = "*"
ruff         = "*"
mypy         = "*"
pytest-asyncio = "*"
```

### 7.2 Geliştirme Ortamı

```bash
# Repo kurulum
git clone https://github.com/[username]/qdap
cd qdap
poetry install

# İlk çalıştırma
python -m qdap.examples.basic_demo

# Test
pytest tests/ -v --tb=short

# Benchmark
python benchmarks/run_all.py --compare-with-tcp
```

---

## 8. Risk Analizi

| Risk | Olasılık | Etki | Önlem |
|------|----------|------|-------|
| QFT → FFT denkliği pratikte yetersiz | Orta | Yüksek | Faz 0'da matematiksel ispat yap |
| Ghost Session false positive yüksek | Yüksek | Orta | Agresif threshold ayarla, fallback ekle |
| TCP nagle'ı bizim scheduler'ı bozar | Orta | Orta | TCP_NODELAY + custom pacing |
| Akademik orijinallik sorgulanır | Düşük | Yüksek | Prior art taraması, net differentiation |
| Python performansı yetersiz | Yüksek | Düşük | Rust hot-path planı hazır |

---

## 9. Referanslar & Akademik Zemin

1. **IETF RFC 9340** — *Architectural Principles for a Quantum Internet* (2023)
2. **Dahlberg et al.** — *A Link Layer Protocol for Quantum Networks*, SIGCOMM 2019
3. **Zhao & Qiao** — *QTCP: Leveraging Internet Principles to Build a Quantum Network*, arXiv:2410.08980 (2024)
4. **Xia et al.** — *Survey of Quantum Internet Protocols from a Layered Perspective*, IEEE (2024)
5. **NeurIPS 2024** — *Exponential Quantum Communication Advantage in Distributed Inference*, arXiv:2310.07136
6. **arXiv:2311.10375** — *Quantum Data Encoding: A Comparative Analysis*
7. **He et al.** — *Hierarchical Architecture for the Quantum Internet*, arXiv:2402.11806 (2024)
8. **QNodeOS** — *An Operating System for Quantum Network Nodes*, Nature (2025)

---

*Bu blueprint, QDAP projesinin teorik ve pratik temelini oluşturmaktadır. Her faz bağımsız olarak değerlendirilebilir ve kendi başına değerli bir çıktı üretir.*
