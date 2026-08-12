from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.cache import SQLiteCache
from app.config import Settings
from app.http import FetchResponse
from app.metrics import MetricsStore
from app.models import FilterMode, MediaType, PublicMediaType, SortMode, SortOrder
from app.nyaa.service import NyaaService


class FixtureFetcher:
    def __init__(self, rss: str, detail: str):
        self.rss = rss
        self.detail = detail
        self.urls: list[str] = []

    async def get(self, url: str) -> FetchResponse:
        self.urls.append(url)
        text = self.detail if "/view/" in url else self.rss
        return FetchResponse(text=text, url=url, headers={}, status_code=200)

    def snapshot(self) -> dict[str, object]:
        return {"last_success_at": None, "last_error": None, "in_flight": 0}


def make_service(workspace_tmp, sample_rss, sample_detail):
    settings = Settings(app_env="test", db_path=workspace_tmp / "cache.sqlite3")
    metrics = MetricsStore()
    cache = SQLiteCache(
        settings.db_path,
        hard_limit_bytes=settings.data_hard_limit_bytes,
        db_target_bytes=settings.cache_db_target_bytes,
        wal_hard_limit_bytes=settings.sqlite_wal_hard_limit_bytes,
        max_entry_bytes=settings.max_cache_entry_bytes,
        stale_grace_seconds=settings.cache_stale_grace_seconds,
        memory_entries=settings.cache_memory_entries,
        metrics=metrics,
    )
    fetcher = FixtureFetcher(sample_rss, sample_detail)
    return NyaaService(settings=settings, fetcher=fetcher, cache=cache, metrics=metrics), fetcher


@pytest.mark.asyncio
async def test_listing_is_hard_scoped_and_excludes_magazines(workspace_tmp, sample_rss, sample_detail):
    service, fetcher = make_service(workspace_tmp, sample_rss, sample_detail)
    result = await service.listing(
        query=None,
        page=1,
        limit=25,
        filter_mode=FilterMode.all,
        media_type=PublicMediaType.all,
        sort=SortMode.date,
        order=SortOrder.desc,
    )

    assert [item["media_type"] for item in result.data["items"]] == [MediaType.manga, MediaType.light_novel]
    assert any(warning.code == "CATEGORY_MISMATCH_REJECTED" for warning in result.warnings)
    assert parse_qs(urlparse(fetcher.urls[0]).query)["c"] == ["3_1"]

    cached = await service.listing(
        query=None,
        page=1,
        limit=25,
        filter_mode=FilterMode.all,
        media_type=PublicMediaType.all,
        sort=SortMode.date,
        order=SortOrder.desc,
    )
    assert cached.cached
    assert len(fetcher.urls) == 1


@pytest.mark.asyncio
async def test_quality_filter_is_enforced_locally(workspace_tmp, sample_rss, sample_detail):
    service, _ = make_service(workspace_tmp, sample_rss, sample_detail)
    result = await service.listing(
        query="example",
        page=1,
        limit=25,
        filter_mode=FilterMode.trusted,
        media_type=PublicMediaType.all,
        sort=SortMode.date,
        order=SortOrder.desc,
    )
    assert len(result.data["items"]) == 1
    assert result.data["items"][0]["trusted"] is True


@pytest.mark.asyncio
async def test_detail_enrichment_uses_file_evidence(workspace_tmp, sample_rss, sample_detail):
    service, _ = make_service(workspace_tmp, sample_rss, sample_detail)
    result = await service.get_detail(
        torrent_id=1234567,
        include_description=True,
        include_raw=True,
        include_files=True,
        files_offset=1,
        files_limit=1,
    )
    assert result.data["category_id"] == "3_1"
    assert result.data["media_type"] == "manga"
    assert result.data["files"]["total"] == 3
    assert len(result.data["files"]["items"]) == 1
    assert result.data["files"]["has_more"] is True
