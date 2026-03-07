# QDAP'a Katkı Rehberi

## İlk Katkın İçin En Kolay Başlangıç Noktaları

### `good first issue` etiketli konular
- Yeni kanal modeli ekle (log-normal, Rayleigh fading)
- Benchmark'a yeni senaryo ekle (WebSocket trafik profili)
- Wireshark dissector'ı geliştir
- Daha fazla dil: Rust/Go port

### Geliştirme Ortamı

```bash
git clone https://github.com/qdap-protocol/qdap
cd qdap
pip install -e ".[dev]"
pytest tests/ -v          # Tüm testler geçmeli
```

## PR Kuralları

1. Her PR için test yaz
2. Benchmark değişikliklerini belgele
3. Yeni quantum analoji eklerken teorik zemin göster
4. `paper/` değişikliklerinde LaTeX derlenmeli

## Code Style

- Python 3.11+, type hints zorunlu
- asyncio preferred (sync fallback OK)
- Docstring: Google style

## Proje Felsefesi

QDAP'ın her bileşeni bir quantum prensibine dayanır.
Yeni özellik önerirken şu soruyu sor:
**"Bu hangi quantum konseptinden ilham alıyor?"**
