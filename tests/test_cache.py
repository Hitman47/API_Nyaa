from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.cache import SQLiteCache


def make_cache(tmp_path, **overrides):
    values = {
        "hard_limit_bytes": 3_000_000,
        "db_target_bytes": 1_500_000,
        "wal_hard_limit_bytes": 500_000,
        "max_entry_bytes": 100_000,
        "stale_grace_seconds": 3600,
        "memory_entries": 10,
    }
    values.update(overrides)
    return SQLiteCache(tmp_path / "cache.sqlite3", **values)


def test_positive_cache_round_trip(workspace_tmp):
    cache = make_cache(workspace_tmp)
    now = datetime.now(UTC)
    assert cache.set(
        "key",
        kind="search",
        payload={"source_url": "https://nyaa.si", "found": True, "data": {"items": [1]}, "warnings": []},
        fingerprint="sha256:test",
        fetched_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    record = cache.get("key")
    assert record is not None
    assert record.fresh
    assert record.payload["data"]["items"] == [1]


def test_stale_record_remains_available(workspace_tmp):
    cache = make_cache(workspace_tmp)
    now = datetime.now(UTC)
    cache.set(
        "stale",
        kind="search",
        payload={"source_url": "https://nyaa.si", "found": True, "data": [1], "warnings": []},
        fingerprint="sha256:stale",
        fetched_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    record = cache.get("stale")
    assert record is not None
    assert not record.fresh
    assert record.stale_usable


def test_negative_cache_expires_and_oversized_entry_is_skipped(workspace_tmp):
    cache = make_cache(workspace_tmp, max_entry_bytes=20_000)
    cache.set_negative("missing", code="RESOURCE_NOT_FOUND", detail="missing", ttl_seconds=60)
    assert cache.get_negative("missing").code == "RESOURCE_NOT_FOUND"
    now = datetime.now(UTC)
    assert not cache.set(
        "huge",
        kind="detail",
        payload={"data": "x" * 30_000},
        fingerprint="sha256:huge",
        fetched_at=now,
        expires_at=now + timedelta(minutes=1),
    )


def test_cache_never_stabilizes_above_hard_limit(workspace_tmp):
    cache = make_cache(workspace_tmp)
    now = datetime.now(UTC)
    for index in range(100):
        cache.set(
            f"key-{index}",
            kind="search",
            payload={"source_url": "https://nyaa.si", "found": True, "data": "x" * 40_000, "warnings": []},
            fingerprint=f"sha256:{index}",
            fetched_at=now,
            expires_at=now + timedelta(days=1),
        )
    cache.maintenance()
    assert cache.data_usage_bytes() <= cache.hard_limit_bytes


def test_debug_diagnostics_are_compressed_and_bounded(workspace_tmp):
    cache = make_cache(workspace_tmp)
    first = cache.capture_diagnostic("parse", "<html>broken</html>" * 100, max_total_bytes=10_000)
    assert first is not None
    assert first.name.startswith("debug-parse-")
    assert first.suffix == ".gz"
    assert sum(path.stat().st_size for path in workspace_tmp.glob("debug-*.gz")) <= 10_000
