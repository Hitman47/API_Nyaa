from __future__ import annotations

import httpx
import pytest

from app.exceptions import ResourceNotFound, UpstreamFetchError
from app.http import AsyncFetcher
from app.rate_limit import ClientRateLimiter, UpstreamLimiter


def make_fetcher(handler):
    return AsyncFetcher(
        user_agent="API_Nyaa/test",
        timeout_seconds=2,
        max_retries=0,
        allowed_hosts={"nyaa.si"},
        limiter=UpstreamLimiter(5, 1),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_fetcher_rejects_cross_host_redirect():
    fetcher = make_fetcher(lambda request: httpx.Response(302, headers={"Location": "https://evil.example/item"}))
    try:
        with pytest.raises(UpstreamFetchError):
            await fetcher.get("https://nyaa.si/?page=rss&c=3_1")
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_fetcher_follows_allowed_redirect_and_maps_404():
    def redirect_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, text="ok")

    fetcher = make_fetcher(redirect_handler)
    try:
        response = await fetcher.get("https://nyaa.si/start")
        assert response.text == "ok"
        assert response.url == "https://nyaa.si/final"
    finally:
        await fetcher.close()

    not_found = make_fetcher(lambda request: httpx.Response(404))
    try:
        with pytest.raises(ResourceNotFound):
            await not_found.get("https://nyaa.si/view/1")
    finally:
        await not_found.close()


def test_client_limiter_never_exposes_bearer_token():
    limiter = ClientRateLimiter(requests=1, window_seconds=60)
    identity = limiter.identity("127.0.0.1", "Bearer very-secret")
    assert identity.startswith("token:")
    assert "very-secret" not in identity
    assert limiter.check(identity)[0] is True
    allowed, remaining, retry_after = limiter.check(identity)
    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1
