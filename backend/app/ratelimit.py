"""Fixed-window rate limits. Redis in production (shared across restarts), in-process for dev/tests.

Limits are keyed by identifier or user, never by IP alone: a whole classroom shares one campus NAT address."""
import threading
import time

from fastapi import HTTPException

from app import config


class _Memory:
    def __init__(self):
        self._d: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def incr(self, key: str, window_s: int) -> int:
        now = time.monotonic()
        with self._lock:
            if len(self._d) > 20_000:
                self._d = {k: v for k, v in self._d.items() if v[0] > now}
            exp, n = self._d.get(key, (0.0, 0))
            if exp <= now:
                exp, n = now + window_s, 0
            self._d[key] = (exp, n + 1)
            return n + 1

    def get(self, key: str) -> int:
        with self._lock:
            exp, n = self._d.get(key, (0.0, 0))
            return n if exp > time.monotonic() else 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)


class _Redis:
    def __init__(self, url: str):
        import redis
        self._r = redis.Redis.from_url(url, socket_timeout=0.5)

    def incr(self, key: str, window_s: int) -> int:
        p = self._r.pipeline()
        p.incr(key)
        p.expire(key, window_s, nx=True)
        return int(p.execute()[0])

    def get(self, key: str) -> int:
        return int(self._r.get(key) or 0)

    def reset(self, key: str) -> None:
        self._r.delete(key)


_store = _Redis(config.REDIS_URL) if config.REDIS_URL else _Memory()


def hit(bucket: str, key: str, limit: int, window_s: int) -> None:
    """Count one event; raise 429 once the window's limit is exceeded. A Redis outage never blocks users."""
    if not config.RATE_LIMIT:
        return
    try:
        n = _store.incr(f"rl:{bucket}:{key}", window_s)
    except Exception:
        return
    if n > limit:
        raise HTTPException(429, "RATE_LIMITED")


def blocked(bucket: str, key: str, limit: int) -> None:
    """Raise 429 if `limit` events are already counted in the window (does not count)."""
    if not config.RATE_LIMIT:
        return
    try:
        n = _store.get(f"rl:{bucket}:{key}")
    except Exception:
        return
    if n >= limit:
        raise HTTPException(429, "RATE_LIMITED")


def reset(bucket: str, key: str) -> None:
    try:
        _store.reset(f"rl:{bucket}:{key}")
    except Exception:
        pass


def _clear_all_for_tests() -> None:
    if isinstance(_store, _Memory):
        _store._d.clear()
