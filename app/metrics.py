from __future__ import annotations

import threading
import time
from collections import Counter, deque
from collections.abc import Iterator
from contextlib import contextmanager


class MetricsStore:
    def __init__(self, max_events: int = 50):
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._timings: dict[str, deque[float]] = {}
        self._events: deque[dict[str, object]] = deque(maxlen=max_events)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def timing(self, name: str, seconds: float) -> None:
        with self._lock:
            values = self._timings.setdefault(name, deque(maxlen=100))
            values.append(max(0.0, seconds))

    def event(self, kind: str, **fields: object) -> None:
        safe = {key: value for key, value in fields.items() if key not in {"token", "query", "magnet_url"}}
        safe["kind"] = kind
        safe["at_monotonic"] = round(time.monotonic(), 3)
        with self._lock:
            self._events.append(safe)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.timing(name, time.monotonic() - started)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            timings = {}
            for name, values in self._timings.items():
                if not values:
                    continue
                sorted_values = sorted(values)
                timings[name] = {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values) * 1000, 2),
                    "p95_ms": round(sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * 0.95))] * 1000, 2),
                }
            return {
                "uptime_seconds": round(time.monotonic() - self.started_at, 3),
                "counters": dict(self._counters),
                "timings": timings,
                "events": list(self._events),
            }
