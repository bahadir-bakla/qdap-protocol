# QDAP — 64KB Chunk Strategy Fix
## SecureGhostSession QFT Scheduler'ı Etkilememeli

---

## Sorun

```
v5_clean benchmark:
  64KB → chunk_strategy: MEDIUM 64KB, frames=200, avg_chunk=64KB ✅

v5_secure benchmark:
  64KB → chunk_strategy: MEDIUM 64KB, frames=400, avg_chunk=32KB ❌

SecureGhostSession kullanılınca QFT Scheduler
yarı boyutlu chunk seçiyor (64KB → 32KB).
Bu yanlış — şifreleme transport kararını etkilememeli.
```

## Kök Neden

```
AES-GCM her frame'e 28 byte ekliyor (12 nonce + 16 tag).
QFTScheduler bunu overhead olarak görüp
"payload büyüdü" sanıyor → daha küçük chunk seçiyor.

Bu davranış YANLIŞ çünkü:
  Chunk kararı sadece network condition'a bakmalı
  (RTT, loss, bandwidth) — şifreleme overhead'i
  application katmanında, network'ü etkilemiyor.
```

## Fix: Şu Dosyayı Kontrol Et ve Düzelt

```
src/qdap/chunking/adaptive_chunker.py

_send_chunked() metodunu bul.
payload boyutunu QFTScheduler'a gönderirken
şifreli boyutu değil orijinal boyutu kullan.
```

## Tam Fix

```python
# src/qdap/chunking/adaptive_chunker.py

# _send_chunked() içinde:

async def _send_chunked(self, payload: bytes, deadline_ms: float) -> None:
    # YANLIŞ — şifrelenmiş boyutu gönderme:
    # encrypted = self._encryptor.pack(payload)
    # self._scheduler.observe_packet_size(len(encrypted))  # ← YANLIŞ

    # DOĞRU — orijinal payload boyutunu kullan:
    self._scheduler.observe_packet_size(len(payload))  # ← şifrelemeden ÖNCE

    strategy = self._scheduler.select_strategy(len(payload))
    chunk_size = strategy.chunk_size_bytes

    chunks = [
        payload[i:i + chunk_size]
        for i in range(0, len(payload), chunk_size)
    ]

    # Şifreleme chunks oluşturulduktan SONRA yapılır
    # Her chunk ayrı şifrelenir — ama chunk kararı zaten verildi
    for chunk in chunks:
        if self._encryptor:
            wire = self._encryptor.pack(chunk)
        else:
            wire = chunk
        await self._transport.send(wire)
```

## Eğer observe_packet_size bulunamıyorsa

```
QFTScheduler içinde şunu ara:
  def observe_packet_size(self, size: int)
  veya
  def _update_energy_bands(self, size: int)
  veya
  self._last_packet_size = size

Hangisi payload boyutunu alıyorsa —
  orijinal (şifrelenmemiş) boyutu al
  şifreli boyutu alma
```

## Doğrulama

```bash
# Fix sonrası v5_secure benchmark tekrar çalıştır
# Sadece 64KB satırını kontrol et:

# Beklenen:
#   avg_chunk_kb: 64.0   (önceki: 32.0 ❌)
#   frames_sent:  200    (önceki: 400 ❌)
#   ratio:        ~1.2-1.4× (önceki: 13.72× ❌)

# Eğer hâlâ 32KB çıkarsa:
#   grep -r "observe_packet_size\|chunk_size\|select_strategy" src/qdap/chunking/
#   Ve tam kod bağlamını bize gönder
```

## Teslim

```
Sadece şunu gönder:
  adaptive_benchmark_v5_secure_fixed.json
  
İçinde 64KB satırı:
  avg_chunk_kb: 64.0 olmalı
  frames_sent: 200 olmalı
  ratio: 1.0-1.5× arası olmalı (mantıklı)

224 test hâlâ geçmeli.
```

## DOKUNMA

```
Sadece şu dosyayı değiştir:
  src/qdap/chunking/adaptive_chunker.py
  (QFTScheduler'a payload boyutu gönderen satır)

Başka hiçbir şeye DOKUNMA.
```
