"""Per-student, per-window claim: SET nonce:{session}:{window}:{student} NX EX 5.

Many students share one QR token, so the key includes the student. Redis in production; a thread-safe
in-process fallback for dev/tests."""
import threading
import time

from app import config


class _MemoryStore:
    def __init__(self):
        self._d: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, ttl_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._d) > 10_000:
                self._d = {k: v for k, v in self._d.items() if v > now}
            if self._d.get(key, 0) > now:
                return False
            self._d[key] = now + ttl_s
            return True


class _RedisStore:
    def __init__(self, url: str):
        import redis
        self._r = redis.Redis.from_url(url, socket_timeout=0.5)

    def claim(self, key: str, ttl_s: int) -> bool:
        return bool(self._r.set(key, 1, nx=True, ex=ttl_s))


_store = _RedisStore(config.REDIS_URL) if config.REDIS_URL else _MemoryStore()


def claim(session_id: str, window: int, student_id: str) -> bool:
    return _store.claim(f"nonce:{session_id}:{window}:{student_id}", config.NONCE_TTL_S)
