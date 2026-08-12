from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import FilterMode, SortMode, SortOrder
from app.nyaa.query import QueryBuilder


def test_category_is_permanently_locked(workspace_tmp):
    with pytest.raises(ValidationError):
        Settings(app_env="test", nyaa_category_id="1_2", db_path=workspace_tmp / "cache.sqlite3")


def test_production_rejects_higher_data_limit(workspace_tmp):
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            data_hard_limit_bytes=350_000_001,
            nyaa_base_url="https://nyaa.si",
            db_path=workspace_tmp / "cache.sqlite3",
        )


def test_production_rejects_mirror_host(workspace_tmp):
    with pytest.raises(ValidationError):
        Settings(
            app_env="production", nyaa_base_url="https://mirror.example", db_path=workspace_tmp / "cache.sqlite3"
        )


def test_query_builder_keeps_exactly_one_locked_category(workspace_tmp):
    settings = Settings(
        app_env="test", nyaa_base_url="https://nyaa.si", db_path=workspace_tmp / "cache.sqlite3"
    )
    url = QueryBuilder(settings).build_rss(
        query="title&c=1_2",
        page=2,
        filter_mode=FilterMode.trusted,
        sort=SortMode.seeders,
        order=SortOrder.desc,
    )
    params = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert params["c"] == ["3_1"]
    assert params["q"] == ["title&c=1_2"]
    assert params["f"] == ["2"]
    assert params["p"] == ["2"]
    assert params["s"] == ["seeders"]


def test_hash_search_is_scoped(workspace_tmp):
    settings = Settings(app_env="test", db_path=workspace_tmp / "cache.sqlite3")
    url = QueryBuilder(settings).build_hash_search("A" * 40)
    assert parse_qs(urlparse(url).query)["c"] == ["3_1"]
