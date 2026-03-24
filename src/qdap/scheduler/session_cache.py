"""
Device-based QFT session state cache.

Her device_id için θ vektörünü ve kanal profilini saklar.
TTL: 300 saniye (5 dakika) — eski profil yanıltıcı olabilir.

Thread-safe: RLock ile korunur.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


TTL_SECONDS = 300  # 5 dakika


@dataclass
class SessionProfile:
    """Bir cihazın kaydedilmiş kanal profili."""
    device_id:   str
    theta:       List[float]          # log-linear ağırlık vektörü
    n_decisions: int                  # toplam karar sayısı
    last_seen:   float = field(default_factory=time.time)
    channel_hint: Optional[dict] = None  # son RTT/loss tahmini

    def is_expired(self, ttl: float = TTL_SECONDS) -> bool:
        return time.time() - self.last_seen > ttl

    def age_seconds(self) -> float:
        return time.time() - self.last_seen


class SessionCache:
    """
    Thread-safe device session cache.

    Usage:
        cache = SessionCache()

        # Bağlantı başlangıcı
        profile = cache.load(device_id)
        if profile:
            scheduler.theta = profile.theta

        # Bağlantı sonunda
        cache.save(device_id, scheduler.theta, scheduler.n_decisions)
    """

    def __init__(self, ttl: float = TTL_SECONDS):
        self._store: Dict[str, SessionProfile] = {}
        self._lock  = threading.RLock()
        self._ttl   = ttl

    def load(self, device_id: str) -> Optional[SessionProfile]:
        """
        Cihazın kaydedilmiş profilini döndür.
        Expire olmuşsa None döner (sıfırdan başla).
        """
        with self._lock:
            profile = self._store.get(device_id)
            if profile is None:
                return None
            if profile.is_expired(self._ttl):
                del self._store[device_id]
                return None
            return profile

    def save(
        self,
        device_id:    str,
        theta:        List[float],
        n_decisions:  int,
        channel_hint: Optional[dict] = None,
    ) -> None:
        """Cihazın güncel profilini kaydet."""
        with self._lock:
            self._store[device_id] = SessionProfile(
                device_id=device_id,
                theta=list(theta),
                n_decisions=n_decisions,
                last_seen=time.time(),
                channel_hint=channel_hint,
            )

    def evict_expired(self) -> int:
        """Expire olmuş kayıtları temizle. Silinen sayısını döndür."""
        with self._lock:
            expired = [
                k for k, v in self._store.items()
                if v.is_expired(self._ttl)
            ]
            for k in expired:
                del self._store[k]
            return len(expired)

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_profiles":   len(self._store),
                "expired_profiles": sum(
                    1 for v in self._store.values() if v.is_expired(self._ttl)
                ),
                "avg_decisions":    (
                    sum(v.n_decisions for v in self._store.values())
                    / max(len(self._store), 1)
                ),
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
