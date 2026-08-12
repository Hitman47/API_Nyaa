from __future__ import annotations

import gzip
import json
import os
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.metrics import MetricsStore


@dataclass(slots=True)
class CacheRecord:
    key: str
    payload: dict[str, Any]
    fingerprint: str
    fetched_at: datetime
    expires_at: datetime
    stale_until: datetime

    @property
    def fresh(self) -> bool:
        return datetime.now(UTC) <= self.expires_at

    @property
    def stale_usable(self) -> bool:
        return datetime.now(UTC) <= self.stale_until


@dataclass(slots=True)
class NegativeRecord:
    code: str
    detail: str
    expires_at: datetime


class SQLiteCache:
    def __init__(
        self,
        db_path: Path,
        *,
        hard_limit_bytes: int,
        db_target_bytes: int,
        wal_hard_limit_bytes: int,
        max_entry_bytes: int,
        stale_grace_seconds: int,
        busy_timeout_ms: int = 5_000,
        memory_entries: int = 512,
        metrics: MetricsStore | None = None,
    ):
        self.db_path = Path(db_path)
        self.data_dir = self.db_path.parent
        self.hard_limit_bytes = hard_limit_bytes
        self.db_target_bytes = db_target_bytes
        self.wal_hard_limit_bytes = wal_hard_limit_bytes
        self.max_entry_bytes = max_entry_bytes
        self.stale_grace_seconds = stale_grace_seconds
        self.busy_timeout_ms = busy_timeout_ms
        self.memory_entries = memory_entries
        self.metrics = metrics
        self._lock = threading.RLock()
        self._memory: OrderedDict[str, CacheRecord] = OrderedDict()
        self._writes_enabled = True
        self._last_prune_reason: str | None = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(self.data_dir, os.W_OK):
            raise OSError(f"Cache directory is not writable: {self.data_dir}")
        self._initialize()

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _dt(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=max(0.1, self.busy_timeout_ms / 1000),
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            max_pages = max(1, self.db_target_bytes // page_size)
            connection.execute(f"PRAGMA max_page_count={max_pages}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    stale_until TEXT NOT NULL,
                    accessed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cache_expiry
                    ON cache_entries(expires_at, accessed_at);
                CREATE TABLE IF NOT EXISTS negative_entries (
                    key TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_negative_expiry
                    ON negative_entries(expires_at);
                """
            )

    def _memory_put(self, record: CacheRecord) -> None:
        if self.memory_entries <= 0:
            return
        self._memory[record.key] = record
        self._memory.move_to_end(record.key)
        while len(self._memory) > self.memory_entries:
            self._memory.popitem(last=False)

    def get(self, key: str) -> CacheRecord | None:
        with self._lock:
            memory = self._memory.get(key)
            if memory and memory.stale_usable:
                self._memory.move_to_end(key)
                if self.metrics:
                    self.metrics.increment("cache_memory_hits")
                return memory
            if memory:
                self._memory.pop(key, None)
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT key, payload, fingerprint, fetched_at, expires_at, stale_until "
                    "FROM cache_entries WHERE key = ?",
                    (key,),
                ).fetchone()
            if not row:
                if self.metrics:
                    self.metrics.increment("cache_misses")
                return None
            record = CacheRecord(
                key=row["key"],
                payload=json.loads(row["payload"]),
                fingerprint=row["fingerprint"],
                fetched_at=self._dt(row["fetched_at"]),
                expires_at=self._dt(row["expires_at"]),
                stale_until=self._dt(row["stale_until"]),
            )
            if not record.stale_usable:
                self.delete(key)
                return None
            self._memory_put(record)
            if self.metrics:
                self.metrics.increment("cache_disk_hits")
            return record

    def set(
        self,
        key: str,
        *,
        kind: str,
        payload: dict[str, Any],
        fingerprint: str,
        fetched_at: datetime,
        expires_at: datetime,
    ) -> bool:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        payload_bytes = len(serialized.encode("utf-8"))
        if payload_bytes > self.max_entry_bytes:
            if self.metrics:
                self.metrics.increment("cache_entry_too_large")
            return False
        with self._lock:
            self._prepare_for_write(payload_bytes)
            if not self._writes_enabled:
                if self.metrics:
                    self.metrics.increment("cache_writes_skipped")
                return False
            stale_until = expires_at.timestamp() + self.stale_grace_seconds
            stale_dt = datetime.fromtimestamp(stale_until, tz=UTC)
            now_text = self._iso(datetime.now(UTC))
            try:
                with self._connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO cache_entries
                            (key, kind, payload, fingerprint, fetched_at, expires_at, stale_until, accessed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            kind=excluded.kind,
                            payload=excluded.payload,
                            fingerprint=excluded.fingerprint,
                            fetched_at=excluded.fetched_at,
                            expires_at=excluded.expires_at,
                            stale_until=excluded.stale_until,
                            accessed_at=excluded.accessed_at
                        """,
                        (
                            key,
                            kind,
                            serialized,
                            fingerprint,
                            self._iso(fetched_at),
                            self._iso(expires_at),
                            self._iso(stale_dt),
                            now_text,
                        ),
                    )
            except (sqlite3.Error, OSError):
                if self.metrics:
                    self.metrics.increment("cache_write_errors")
                return False
            record = CacheRecord(key, payload, fingerprint, fetched_at, expires_at, stale_dt)
            self._memory_put(record)
            self._enforce_after_write(key)
            if self.metrics:
                self.metrics.increment("cache_writes")
            return key in self._memory or self._exists(key)

    def _exists(self, key: str) -> bool:
        with self._connection() as connection:
            return connection.execute("SELECT 1 FROM cache_entries WHERE key = ?", (key,)).fetchone() is not None

    def set_negative(self, key: str, *, code: str, detail: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + ttl_seconds, tz=UTC)
        with self._lock:
            self._prepare_for_write(len(detail.encode("utf-8")) + 256)
            if not self._writes_enabled:
                return
            try:
                with self._connection() as connection:
                    connection.execute(
                        "INSERT INTO negative_entries(key, code, detail, expires_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET code=excluded.code, detail=excluded.detail, expires_at=excluded.expires_at",
                        (key, code, detail[:1_000], self._iso(expires_at)),
                    )
            except sqlite3.Error:
                if self.metrics:
                    self.metrics.increment("negative_cache_write_errors")

    def get_negative(self, key: str) -> NegativeRecord | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT code, detail, expires_at FROM negative_entries WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            record = NegativeRecord(row["code"], row["detail"], self._dt(row["expires_at"]))
            if datetime.now(UTC) > record.expires_at:
                connection.execute("DELETE FROM negative_entries WHERE key = ?", (key,))
                return None
            if self.metrics:
                self.metrics.increment("negative_cache_hits")
            return record

    def delete(self, key: str) -> None:
        with self._lock:
            self._memory.pop(key, None)
            with self._connection() as connection:
                connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))

    def data_usage_bytes(self) -> int:
        total = 0
        try:
            for path in self.data_dir.iterdir():
                if path.is_file():
                    total += path.stat().st_size
        except OSError:
            return self.hard_limit_bytes
        return total

    def capture_diagnostic(self, kind: str, content: str, *, max_total_bytes: int) -> Path | None:
        """Persist a bounded compressed parser diagnostic without raw query data in its name."""

        if max_total_bytes <= 0:
            return None
        safe_kind = "".join(character for character in kind.casefold() if character.isalnum() or character == "-")[:24]
        encoded = content.encode("utf-8", errors="replace")[:2_000_000]
        compressed = gzip.compress(encoded, compresslevel=6)
        if len(compressed) > min(max_total_bytes, 2_000_000):
            return None
        with self._lock:
            existing = sorted(
                self.data_dir.glob("debug-*.gz"),
                key=lambda path: path.stat().st_mtime,
            )
            diagnostic_usage = sum(path.stat().st_size for path in existing)
            while existing and (
                diagnostic_usage + len(compressed) > max_total_bytes
                or self.data_usage_bytes() + len(compressed) >= self.hard_limit_bytes
            ):
                oldest = existing.pop(0)
                size = oldest.stat().st_size
                oldest.unlink(missing_ok=True)
                diagnostic_usage -= size
            if (
                diagnostic_usage + len(compressed) > max_total_bytes
                or self.data_usage_bytes() + len(compressed) >= self.hard_limit_bytes
            ):
                return None
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            destination = self.data_dir / f"debug-{safe_kind or 'parser'}-{timestamp}-{uuid4().hex[:8]}.html.gz"
            temporary = destination.with_suffix(".tmp")
            try:
                temporary.write_bytes(compressed)
                os.replace(temporary, destination)
            except OSError:
                temporary.unlink(missing_ok=True)
                return None
            if self.metrics:
                self.metrics.increment("diagnostic_captures")
            return destination

    def _wal_size(self) -> int:
        path = Path(f"{self.db_path}-wal")
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _checkpoint(self, *, truncate: bool = False) -> None:
        mode = "TRUNCATE" if truncate else "PASSIVE"
        try:
            with self._connection() as connection:
                connection.execute(f"PRAGMA wal_checkpoint({mode})")
        except sqlite3.Error:
            if self.metrics:
                self.metrics.increment("cache_checkpoint_errors")

    def _prepare_for_write(self, estimated_bytes: int) -> None:
        usage = self.data_usage_bytes()
        if self._wal_size() >= int(self.wal_hard_limit_bytes * 0.75):
            self._checkpoint(truncate=True)
            usage = self.data_usage_bytes()
        projected = usage + max(estimated_bytes * 2, 64_000)
        if projected >= int(self.hard_limit_bytes * 0.90):
            self.prune(target_bytes=int(self.hard_limit_bytes * 0.82), reason="preventive")
            usage = self.data_usage_bytes()
        self._writes_enabled = usage + max(estimated_bytes * 2, 64_000) < self.hard_limit_bytes

    def _enforce_after_write(self, key: str) -> None:
        if self._wal_size() > self.wal_hard_limit_bytes:
            self._checkpoint(truncate=True)
        if self.data_usage_bytes() > self.hard_limit_bytes:
            self.delete(key)
            self._checkpoint(truncate=True)
            self.prune(target_bytes=int(self.hard_limit_bytes * 0.82), reason="hard_limit")
        self._writes_enabled = self.data_usage_bytes() < self.hard_limit_bytes

    def prune(self, *, target_bytes: int | None = None, reason: str = "maintenance") -> int:
        target = target_bytes or int(self.hard_limit_bytes * 0.82)
        deleted = 0
        now = self._iso(datetime.now(UTC))
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM negative_entries WHERE expires_at < ?", (now,))
            while self.data_usage_bytes() > target:
                rows = connection.execute(
                    "SELECT key FROM cache_entries ORDER BY "
                    "CASE WHEN expires_at < ? THEN 0 ELSE 1 END, accessed_at ASC LIMIT 100",
                    (now,),
                ).fetchall()
                if not rows:
                    diagnostics = sorted(
                        self.data_dir.glob("debug-*.gz"),
                        key=lambda path: path.stat().st_mtime,
                    )
                    if not diagnostics:
                        break
                    diagnostics[0].unlink(missing_ok=True)
                    continue
                keys = [row["key"] for row in rows]
                connection.executemany("DELETE FROM cache_entries WHERE key = ?", [(key,) for key in keys])
                for key in keys:
                    self._memory.pop(key, None)
                deleted += len(keys)
                connection.commit()
                self._checkpoint(truncate=True)
        self._last_prune_reason = reason
        if self.metrics and deleted:
            self.metrics.increment("cache_pruned_entries", deleted)
        return deleted

    def maintenance(self) -> dict[str, Any]:
        deleted = self.prune(reason="scheduled")
        self._checkpoint(truncate=True)
        return {"deleted": deleted, **self.snapshot()}

    def snapshot(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            positive = int(connection.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0])
            negative = int(connection.execute("SELECT COUNT(*) FROM negative_entries").fetchone()[0])
        usage = self.data_usage_bytes()
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "entries": positive,
            "negative_entries": negative,
            "memory_entries": len(self._memory),
            "writes_enabled": self._writes_enabled,
            "last_prune_reason": self._last_prune_reason,
            "data_usage_bytes": usage,
            "data_hard_limit_bytes": self.hard_limit_bytes,
            "usage_percent": round(usage / self.hard_limit_bytes * 100, 2),
            "db_size_bytes": db_size,
            "wal_size_bytes": self._wal_size(),
            "wal_hard_limit_bytes": self.wal_hard_limit_bytes,
        }
