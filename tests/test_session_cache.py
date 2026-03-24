import time
import pytest
from qdap.scheduler.session_cache import SessionCache
from qdap.scheduler.qft_scheduler import QFTScheduler


def test_cache_save_and_load():
    cache = SessionCache(ttl=60)
    theta = [0.1, 0.2, 0.5, 0.1, 0.1]
    cache.save("sensor_01", theta, n_decisions=500)
    profile = cache.load("sensor_01")
    assert profile is not None
    assert profile.theta == theta
    assert profile.n_decisions == 500


def test_cache_ttl_expiry():
    cache = SessionCache(ttl=0.1)  # 100ms TTL
    cache.save("sensor_02", [0.2]*5, n_decisions=100)
    time.sleep(0.2)
    assert cache.load("sensor_02") is None


def test_cache_miss_returns_none():
    cache = SessionCache()
    assert cache.load("nonexistent_device") is None


def test_warmup_skipped_on_resume():
    """
    Session resume ile ikinci bağlantıda warm-up atlanmalı.
    İlk bağlantı: 100 karar → kaydet
    İkinci bağlantı: yükle → n_decisions=100'den başla
    """
    cache = SessionCache()
    s1 = QFTScheduler()

    # İlk bağlantı — 100 karar
    for _ in range(100):
        s1.decide(512, 200.0, 0.15)

    theta_after = list(s1.theta)
    cache.save("icu_monitor_01", s1.theta, s1.n_decisions)

    # İkinci bağlantı
    s2 = QFTScheduler()
    assert not s2.is_warmed_up  # sıfırdan başlıyor

    # Session yükle
    profile = cache.load("icu_monitor_01")
    assert profile is not None
    s2.theta = profile.theta
    s2.n_decisions = profile.n_decisions

    # n_decisions korundu
    assert s2.n_decisions == 100
    assert s2.theta == theta_after


def test_second_connection_better_confidence():
    """
    Aynı kanal profilinden devam eden scheduler
    daha yüksek confidence vermeli.
    decide() ChunkStrategy (IntEnum) döndürür;
    confidence karşılaştırması weights üzerinden yapılır.
    """
    cache = SessionCache()

    # İlk bağlantı — yüksek loss kanalda 201 karar
    s1 = QFTScheduler()
    for _ in range(201):
        s1.decide(512, 200.0, 0.15)
    cache.save("gas_sensor_01", s1.theta, s1.n_decisions)

    # Cold start — tek karar sonrası dominant weight
    s_cold = QFTScheduler()
    s_cold.decide(512, 200.0, 0.15)
    conf_cold = max(s_cold.weights)

    # Session resume — önceki θ yüklendi, tek karar sonrası dominant weight
    s_warm = QFTScheduler()
    profile = cache.load("gas_sensor_01")
    s_warm.theta = profile.theta
    s_warm.n_decisions = profile.n_decisions
    s_warm.decide(512, 200.0, 0.15)
    conf_warm = max(s_warm.weights)

    assert conf_warm > conf_cold, (
        f"Warm resume ({conf_warm:.3f}) should be more confident "
        f"than cold start ({conf_cold:.3f})"
    )


def test_evict_expired():
    cache = SessionCache(ttl=0.05)
    for i in range(10):
        cache.save(f"dev_{i}", [0.2]*5, 50)
    time.sleep(0.1)
    evicted = cache.evict_expired()
    assert evicted == 10
    assert len(cache) == 0


def test_attach_device_resume():
    """attach_device ile global cache'den otomatik yükleme."""
    from qdap.scheduler.qft_scheduler import _global_cache

    s1 = QFTScheduler()
    for _ in range(150):
        s1.decide(512, 200.0, 0.15)
    theta_saved = list(s1.theta)
    ndec_saved = s1.n_decisions

    _global_cache.save("test_attach_dev", s1.theta, s1.n_decisions)

    s2 = QFTScheduler()
    resumed = s2.attach_device("test_attach_dev")

    assert resumed is True
    assert s2.n_decisions == ndec_saved
    assert s2.theta == theta_saved


def test_detach_device_saves_state():
    """detach_device global cache'e otomatik kaydeder."""
    from qdap.scheduler.qft_scheduler import _global_cache

    s = QFTScheduler()
    s._device_id = "test_detach_dev"
    for _ in range(50):
        s.decide(512, 100.0, 0.05)
    theta_before = list(s.theta)
    ndec_before = s.n_decisions

    s.detach_device()

    profile = _global_cache.load("test_detach_dev")
    assert profile is not None
    assert profile.theta == theta_before
    assert profile.n_decisions == ndec_before
    assert s._device_id is None


def test_warmup_progress():
    s = QFTScheduler()
    assert s.warmup_progress == 0.0
    assert not s.is_warmed_up

    for _ in range(512):
        s.decide(512, 50.0, 0.05)

    assert abs(s.warmup_progress - 0.5) < 0.01

    for _ in range(512):
        s.decide(512, 50.0, 0.05)

    assert s.is_warmed_up
    assert s.warmup_progress == 1.0


def test_thread_safety():
    """8 thread eş zamanlı save/load → hata olmamalı."""
    import threading
    cache = SessionCache()
    errors = []

    def worker(tid):
        try:
            for i in range(100):
                dev = f"dev_{tid}_{i % 5}"
                cache.save(dev, [float(tid)] * 5, i)
                cache.load(dev)
                cache.evict_expired()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread safety errors: {errors}"
