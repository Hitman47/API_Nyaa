from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import defaultdict, deque


class ClientRateLimiter:
    def __init__(self, requests: int, window_seconds: int):
        self.requests = requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def identity(client_ip: str, authorization: str | None) -> str:
        if authorization and authorization.lower().startswith("bearer "):
            digest = hashlib.sha256(authorization.encode("utf-8")).hexdigest()[:20]
            return f"token:{digest}"
        return f"ip:{client_ip}"

    def check(self, identity: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets[identity]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.requests:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])) + 1)
                return False, 0, retry_after
            bucket.append(now)
            remaining = max(0, self.requests - len(bucket))
            if len(self._buckets) > 10_000:
                for key in list(self._buckets)[:1_000]:
                    if not self._buckets[key] or self._buckets[key][-1] <= cutoff:
                        self._buckets.pop(key, None)
            return True, remaining, self.window_seconds

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "requests": self.requests,
                "window_seconds": self.window_seconds,
                "active_identities": len(self._buckets),
            }


class UpstreamLimiter:
    def __init__(self, requests_per_second: float, max_concurrency: int):
        self.interval = 1.0 / requests_per_second
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._pace_lock = asyncio.Lock()
        self._last_started = 0.0
        self.max_concurrency = max_concurrency

    async def __aenter__(self) -> UpstreamLimiter:
        await self.semaphore.acquire()
        async with self._pace_lock:
            now = time.monotonic()
            wait_for = self.interval - (now - self._last_started)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_started = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.semaphore.release()

    def snapshot(self) -> dict[str, float | int]:
        return {
            "requests_per_second": round(1.0 / self.interval, 3),
            "max_concurrency": self.max_concurrency,
        }
