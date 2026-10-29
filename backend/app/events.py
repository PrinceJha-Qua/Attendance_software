"""In-process pub/sub for the professor's live feed. Publishing is thread-safe (check-in handlers run in the threadpool).
Single process only: with several workers this moves to Redis pub/sub (phase 9 deploy item)."""
import asyncio
import threading
import time
from collections import deque


class Hub:
    RING = 500

    def __init__(self):
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._ring: dict[str, deque] = {}
        self._subs: dict[str, set] = {}

    def publish(self, sid: str, type_: str, data: dict) -> None:
        with self._lock:
            n = self._seq[sid] = self._seq.get(sid, 0) + 1
            ev = {"seq": n, "type": type_, "data": data, "t": int(time.time() * 1000)}
            self._ring.setdefault(sid, deque(maxlen=self.RING)).append(ev)
            subs = list(self._subs.get(sid, ()))
        for loop, q in subs:
            loop.call_soon_threadsafe(q.put_nowait, ev)

    def subscribe(self, sid: str, since: int):
        """Returns (queue, backlog, current_seq, resync). Registered atomically with the backlog read, so nothing is lost or doubled."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            cur = self._seq.get(sid, 0)
            ring = self._ring.get(sid, deque())
            resync = since > cur or (since < cur and (not ring or ring[0]["seq"] > since + 1))  # unknown seq (restart) or gap larger than the ring
            backlog = [] if resync else [e for e in ring if e["seq"] > since]
            self._subs.setdefault(sid, set()).add((asyncio.get_running_loop(), q))
        return q, backlog, cur, resync

    def unsubscribe(self, sid: str, q) -> None:
        with self._lock:
            self._subs[sid] = {x for x in self._subs.get(sid, set()) if x[1] is not q}

    def recent_checkins(self, sid: str, window_s: float) -> int:
        cutoff = (time.time() - window_s) * 1000
        with self._lock:
            return sum(1 for e in self._ring.get(sid, ()) if e["type"] == "checkin.created" and e["t"] >= cutoff)


hub = Hub()
