from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.models import ListingData
from app.nyaa.service import ServiceResult, fingerprint


class StubService:
    async def listing(self, **kwargs) -> ServiceResult:
        data = ListingData(
            query=kwargs["query"],
            page=kwargs["page"],
            limit=kwargs["limit"],
            has_more=False,
            filter=kwargs["filter_mode"],
            media_type=kwargs["media_type"],
            sort=kwargs["sort"],
            order=kwargs["order"],
            items=[],
        ).model_dump(mode="json")
        now = datetime.now(UTC)
        return ServiceResult(
            data=data,
            source_url="https://nyaa.si/?page=rss&c=3_1",
            found=False,
            cached=False,
            fetched_at=now,
            cache_expires_at=now + timedelta(minutes=5),
            fingerprint=fingerprint(data),
        )


def test_public_docs_and_protected_contract(workspace_tmp):
    app.state.settings = Settings(
        app_env="test",
        api_token="secret",
        db_path=workspace_tmp / "cache.sqlite3",
        rate_limit_enabled=False,
    )
    with TestClient(app) as client:
        app.state.service = StubService()
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/latest").status_code == 401

        response = client.get("/latest", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        assert response.json()["found"] is False
        assert response.json()["source_url"].endswith("c=3_1")
        etag = response.headers["ETag"]
        not_modified = client.get(
            "/latest",
            headers={"Authorization": "Bearer secret", "If-None-Match": etag},
        )
        assert not_modified.status_code == 304


def test_category_and_uploader_parameters_are_rejected(workspace_tmp):
    app.state.settings = Settings(
        app_env="test",
        db_path=workspace_tmp / "cache.sqlite3",
        rate_limit_enabled=False,
    )
    with TestClient(app) as client:
        app.state.service = StubService()
        for query in ("c=3_2", "category=1_2", "uploader=someone"):
            response = client.get(f"/latest?{query}")
            assert response.status_code == 422
            assert response.json()["code"] == "INVALID_PARAMETER"
