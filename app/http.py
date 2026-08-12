from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx

from app.exceptions import ResourceNotFound, UpstreamFetchError
from app.metrics import MetricsStore
from app.rate_limit import UpstreamLimiter


@dataclass(slots=True)
class FetchResponse:
    text: str
    url: str
    headers: dict[str, str]
    status_code: int


class AsyncFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float,
        max_retries: int,
        allowed_hosts: set[str],
        limiter: UpstreamLimiter,
        metrics: MetricsStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.max_retries = max_retries
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        self.limiter = limiter
        self.metrics = metrics
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/html;q=0.9"},
            timeout=httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds)),
            follow_redirects=False,
            transport=transport,
        )

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UpstreamFetchError("Upstream URL is invalid.")
        if parsed.hostname.casefold() not in self.allowed_hosts:
            raise UpstreamFetchError("Upstream redirect target is not allowed.")

    @staticmethod
    def _retry_after(headers: httpx.Headers) -> float | None:
        value = headers.get("Retry-After")
        if not value:
            return None
        try:
            return min(60.0, max(0.0, float(value)))
        except ValueError:
            try:
                return min(60.0, max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return None

    async def get(self, url: str) -> FetchResponse:
        self._validate_url(url)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            current_url = url
            try:
                started = time.monotonic()
                async with self.limiter:
                    response = await self.client.get(current_url)
                redirects = 0
                while response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location or redirects >= 3:
                        raise UpstreamFetchError("Nyaa returned an invalid redirect chain.")
                    current_url = urljoin(str(response.url), location)
                    self._validate_url(current_url)
                    redirects += 1
                    async with self.limiter:
                        response = await self.client.get(current_url)
                if self.metrics:
                    self.metrics.timing("upstream_fetch", time.monotonic() - started)
                    self.metrics.increment(f"upstream_status_{response.status_code}")
                if response.status_code == 404:
                    raise ResourceNotFound("Torrent resource was not found on Nyaa.")
                if response.status_code == 429 or response.status_code >= 500:
                    wait = self._retry_after(response.headers)
                    if attempt < self.max_retries:
                        await asyncio.sleep(wait if wait is not None else (0.5 * (2**attempt) + random.random() * 0.25))
                        continue
                    raise UpstreamFetchError(f"Nyaa returned HTTP {response.status_code}.")
                if response.status_code < 200 or response.status_code >= 300:
                    raise UpstreamFetchError(f"Nyaa returned HTTP {response.status_code}.")
                self.last_success_at = datetime.now(UTC)
                self.last_error = None
                return FetchResponse(response.text, str(response.url), dict(response.headers), response.status_code)
            except ResourceNotFound:
                raise
            except UpstreamFetchError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2**attempt) + random.random() * 0.25)
                    continue
        self.last_error = type(last_error).__name__ if last_error else "unknown"
        if self.metrics:
            self.metrics.increment("upstream_fetch_errors")
        if isinstance(last_error, UpstreamFetchError):
            raise last_error
        raise UpstreamFetchError("Unable to contact Nyaa.") from last_error

    async def close(self) -> None:
        await self.client.aclose()

    def snapshot(self) -> dict[str, object]:
        return {
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error": self.last_error,
            **self.limiter.snapshot(),
        }
