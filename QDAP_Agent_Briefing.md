# QDAP Phase 5 — Ajan Briefing
## Teknik Görevler (GitHub + CI/CD + QUIC + Benchmark)

> **Proje:** QDAP — Quantum-Inspired Dynamic Application Protocol  
> **Mevcut durum:** 158/158 test, 6.96s ✅  
> **Ajana verilen görev:** Teknik altyapı — paper+lansman biz yapıyoruz  
> **Öncelik sırası:** 1 → 2 → 3 → 4 (sırayla yap)

---

## Görev 1 — Gerçek TCP Baseline Benchmark (KRİTİK)

**Neden kritik:** Şu an ACK overhead "~3.91% simüle edilmiş".
Akademik paper için gerçek ölçüm şart. Reviewer bunu sorar.

**Ne yapılacak:**

```python
# benchmarks/baseline/real_tcp_baseline.py

# 1. Saf TCP echo sunucu yaz (asyncio, QDAP yok)
# 2. Aynı veriyi TCP ile gönder, Wireshark/scapy ile ACK byte'larını say
# 3. QDAP ile aynı senaryoyu çalıştır
# 4. Yan yana tablo üret:

# Beklenen sonuç:
# | Senaryo       | TCP Baseline | QDAP    | Δ        |
# |---------------|-------------|---------|----------|
# | Throughput    | X MB/s      | Y MB/s  | +Z%      |
# | p99 latency   | X ms        | Y ms    | -Z%      |
# | ACK overhead  | ~3.91%      | 0.00%   | -100%    |
# | 10K msgs p999 | X ms        | Y ms    | -Z%      |
```

**Scapy kullanmak istemiyorsan alternatif:**
```python
# SO_TIMESTAMPING ile kernel-level ACK sayımı
# veya
# psutil.net_io_counters() before/after ile byte farkı
```

**Çıktı:** `benchmarks/results/real_tcp_vs_qdap.json` + grafik PNG

---

## Görev 2 — QUIC Adapter (aioquic)

**Neden:** TCP'den sonra QUIC, paper'da "transport agnostic" iddiasını kanıtlar.

**Ne yapılacak:**

```python
# src/qdap/transport/quic/adapter.py

# Mevcut base.py interface'ini implemente et:
# - QDAPTransport abstract class var → onu implement et
# - aioquic.asyncio.QuicConnectionProtocol'u extend et
# - Stream map: DATA→stream_0, CTRL→stream_2, GHOST→stream_4
# - TLS self-signed cert üret (test için)

# Referans: Phase 2 Guide'daki 2.2 QUIC Adapter bölümü
# Dosya: /mnt/user-data/outputs/QDAP_Phase2_Guide.md

# Test: tests/transport/test_quic_adapter.py
# En az 5 test: connect, send, recv, multiframe, concurrent
```

**Bağımlılık ekle:**
```toml
# pyproject.toml'a ekle:
aioquic = ">=1.0"
```

**Çıktı:** Çalışan QUIC adapter + testler + TCP vs QUIC benchmark karşılaştırması

---

## Görev 3 — WAN Testi (Loopback Dışı)

**Neden:** Paper'da "loopback only" sınırlamasını kısmen aşmak için.
Tam WAN olmasa da localhost→localhost farklı portlar yeterli değil —
network namespace veya Docker ile gerçekçi gecikme simülasyonu yap.

**Ne yapılacak:**

```bash
# Seçenek A: Linux tc (traffic control) ile gecikme ekle
sudo tc qdisc add dev lo root netem delay 20ms loss 1%
python benchmarks/run_all.py
sudo tc qdisc del dev lo root

# Seçenek B: Docker iki container
# container_a: QDAP server
# container_b: QDAP client
# docker network: --network-opt com.docker.network.driver.mtu=1500

# Seçenek C: macOS'ta network link conditioner
# (System Preferences → Network Link Conditioner)
```

**Test senaryoları:**
```
- delay=20ms, loss=0%    → "home WiFi benzeri"
- delay=50ms, loss=1%    → "4G benzeri"  
- delay=100ms, loss=5%   → "congested network"

Her senaryoda: QDAP vs raw TCP
Özellikle: Ghost Session loss detection F1 gerçek kayıp koşulunda
```

**Çıktı:** `benchmarks/results/wan_simulation.json` + network_conditions.py entegrasyonu

---

## Görev 4 — GitHub Repository + CI/CD + PyPI

**4a. GitHub Actions:**
```yaml
# .github/workflows/test.yml — Phase 5 Guide'daki şablonu kullan
# Dosya: /mnt/user-data/outputs/QDAP_Phase5_Guide.md → "2.3 CI/CD" bölümü

# Ekstra: benchmark smoke test
# .github/workflows/benchmark.yml
# Haftalık çalışır, sonuçları artifact olarak saklar
```

**4b. pyproject.toml:**
```toml
# Phase 5 Guide'daki "2.4 pyproject.toml" bölümünü kullan
# Ekstra: hatch build → dist/ klasörü
# TestPyPI'ya önce yükle: twine upload --repository testpypi dist/*
# Sonra: pip install -i https://test.pypi.org/simple/ qdap
```

**4c. README Demo GIF:**
```bash
# asciinema ile kaydet:
asciinema rec qdap-iot-demo.cast
python -m examples.iot.demo   # 15 saniye çalıştır, Ctrl+C
# Bitir

# svg'ye çevir:
pip install svg-term-cli
# veya: agg (asciinema-agg) kullan → demo.gif

# README'ye ekle:
# ![QDAP IoT Demo](docs/assets/qdap-iot-demo.gif)
```

**4d. Dokümantasyon:**
```
docs/
├── index.md           ← MkDocs homepage
├── quickstart.md      ← 5 dakikada çalışır
├── protocol-spec.md   ← Wire format + algoritma detayları  
├── api-reference.md   ← Python API docs (pydoc-markdown)
└── benchmarks.md      ← Tüm sonuçlar + metodoloji

# MkDocs Material theme:
pip install mkdocs-material
mkdocs build
```

---

## Mevcut Dosya Referansları

Ajana rehber olacak dosyalar (hepsi hazır):

```
/mnt/user-data/outputs/QDAP_Blueprint.md      ← Genel mimari
/mnt/user-data/outputs/QDAP_Phase2_Guide.md   ← TCP/QUIC adapter detayları
/mnt/user-data/outputs/QDAP_Phase3_Guide.md   ← Verification altyapısı
/mnt/user-data/outputs/QDAP_Phase4_Guide.md   ← Demo yapısı
/mnt/user-data/outputs/QDAP_Phase5_Guide.md   ← GitHub/CI/PyPI şablonları
```

---

## Teslim Kriterleri

```
Görev 1 tamamlandı:
  ✓ benchmarks/results/real_tcp_vs_qdap.json mevcut
  ✓ Gerçek ACK overhead sayısı (simüle değil)
  ✓ Grafik PNG üretildi

Görev 2 tamamlandı:
  ✓ tests/transport/test_quic_adapter.py geçiyor (min 5 test)
  ✓ TCP vs QUIC benchmark JSON mevcut
  ✓ Toplam test sayısı artmış (158+)

Görev 3 tamamlandı:
  ✓ En az 2 ağ profili test edildi (20ms+1% loss, 50ms+%5 loss)
  ✓ Ghost Session F1 gerçek kayıp koşulunda ölçüldü

Görev 4 tamamlandı:
  ✓ GitHub Actions CI yeşil (Ubuntu + macOS)
  ✓ pip install -e . temiz çalışıyor
  ✓ Demo GIF kayıtlı
  ✓ MkDocs sitesi build ediliyor

Hepsini bitirince:
  → Bize haber ver: paper + lansman aşamasına geçiyoruz
```

---

## Önemli Notlar

1. **Test sayısı düşmesin** — Her görev sonunda `pytest tests/ -v` çalıştır
2. **Ghost Session WAN'da** — Gerçek gecikme altında `detect_loss()` 
   threshold'u ayarlamak gerekebilir (`2.5 × RTT` formülü var, kontrol et)
3. **QUIC TLS** — Self-signed cert üret, production cert değil
4. **PyPI** — Önce TestPyPI, sonra gerçek PyPI
5. **GIF boyutu** — 2MB altında tut (GitHub README için)
