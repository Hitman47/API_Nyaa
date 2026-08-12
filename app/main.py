from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.auth import reject_scope_parameters, require_api_token
from app.cache import SQLiteCache
from app.config import Settings, get_settings
from app.exceptions import APIError, InvalidParameter, RateLimited
from app.http import AsyncFetcher
from app.metrics import MetricsStore
from app.models import (
    Envelope,
    FilterMode,
    ListingData,
    PublicMediaType,
    ResolveData,
    RuntimeData,
    SortMode,
    SortOrder,
    TorrentDetail,
)
from app.nyaa.service import NyaaService, ServiceResult, fingerprint
from app.rate_limit import ClientRateLimiter, UpstreamLimiter

PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    if settings.log_format.casefold() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )


def build_components(settings: Settings):
    metrics = MetricsStore()
    cache = SQLiteCache(
        settings.db_path,
        hard_limit_bytes=settings.data_hard_limit_bytes,
        db_target_bytes=settings.cache_db_target_bytes,
        wal_hard_limit_bytes=settings.sqlite_wal_hard_limit_bytes,
        max_entry_bytes=settings.max_cache_entry_bytes,
        stale_grace_seconds=settings.cache_stale_grace_seconds,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        memory_entries=settings.cache_memory_entries,
        metrics=metrics,
    )
    upstream_limiter = UpstreamLimiter(
        settings.upstream_requests_per_second,
        settings.upstream_max_concurrency,
    )
    fetcher = AsyncFetcher(
        user_agent=settings.user_agent,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.request_max_retries,
        allowed_hosts=settings.allowed_upstream_hosts,
        limiter=upstream_limiter,
        metrics=metrics,
    )
    service = NyaaService(settings=settings, fetcher=fetcher, cache=cache, metrics=metrics)
    client_limiter = ClientRateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
    return metrics, cache, fetcher, service, client_limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = getattr(app.state, "settings", get_settings())
    configure_logging(settings)
    metrics, cache, fetcher, service, client_limiter = build_components(settings)
    app.state.settings = settings
    app.state.metrics = metrics
    app.state.cache = cache
    app.state.fetcher = fetcher
    app.state.service = service
    app.state.client_limiter = client_limiter
    try:
        yield
    finally:
        await fetcher.close()


settings_for_metadata = get_settings()
app = FastAPI(
    title=settings_for_metadata.app_name,
    version=__version__,
    description=(
        "Unofficial self-hosted JSON API for Nyaa Literature - English-translated. "
        "The upstream category is permanently locked to c=3_1. The service returns metadata, "
        "magnet links and .torrent URLs but never downloads torrent content."
    ),
    docs_url=settings_for_metadata.docs_url,
    redoc_url=settings_for_metadata.redoc_url,
    openapi_url=settings_for_metadata.openapi_url,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context_and_rate_limit(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id", "").strip()[:128] or str(uuid4())
    request.state.request_id = request_id
    settings: Settings = request.app.state.settings
    remaining = settings.rate_limit_requests
    reset = settings.rate_limit_window_seconds
    if settings.rate_limit_enabled and request.url.path not in PUBLIC_PATHS:
        client_ip = request.client.host if request.client else "unknown"
        identity = request.app.state.client_limiter.identity(client_ip, request.headers.get("Authorization"))
        allowed, remaining, reset = request.app.state.client_limiter.check(identity)
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"code": "RATE_LIMITED", "detail": "Client rate limit exceeded.", "request_id": request_id},
                headers={"Retry-After": str(reset)},
            )
            response.headers["X-Request-Id"] = request_id
            response.headers["RateLimit-Limit"] = str(settings.rate_limit_requests)
            response.headers["RateLimit-Remaining"] = "0"
            response.headers["RateLimit-Reset"] = str(reset)
            return response
    started = time.monotonic()
    response = await call_next(request)
    if hasattr(request.app.state, "metrics"):
        request.app.state.metrics.timing("http_request", time.monotonic() - started)
        request.app.state.metrics.increment(f"http_status_{response.status_code}")
    response.headers["X-Request-Id"] = request_id
    if settings.rate_limit_enabled and request.url.path not in PUBLIC_PATHS:
        response.headers["RateLimit-Limit"] = str(settings.rate_limit_requests)
        response.headers["RateLimit-Remaining"] = str(remaining)
        response.headers["RateLimit-Reset"] = str(reset)
    return response


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    headers = {}
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after)
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": exc.detail, "request_id": getattr(request.state, "request_id", "")},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "INVALID_PARAMETER",
            "detail": "Request parameters do not match the API contract.",
            "request_id": getattr(request.state, "request_id", ""),
            "errors": exc.errors(),
        },
    )


def envelope_response(request: Request, result: ServiceResult) -> Response:
    etag = f'"{result.fingerprint}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "X-Data-Fingerprint": result.fingerprint})
    content = {
        "schema_version": "1.0",
        "ok": True,
        "found": result.found,
        "source": "nyaa",
        "source_url": result.source_url,
        "cached": result.cached,
        "fetched_at": result.fetched_at.isoformat(),
        "cache_expires_at": result.cache_expires_at.isoformat(),
        "partial": result.partial,
        "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
        "fingerprint": result.fingerprint,
        "data": result.data,
    }
    return JSONResponse(content=content, headers={"ETag": etag, "X-Data-Fingerprint": result.fingerprint})


business_dependencies = [Depends(require_api_token), Depends(reject_scope_parameters)]


@app.get("/health", tags=["Health"])
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health/runtime", response_model=Envelope[RuntimeData], tags=["Health"], dependencies=business_dependencies)
async def runtime_health(request: Request):
    data = request.app.state.service.runtime_data(request.app.state.client_limiter.snapshot())
    now = datetime.now(UTC)
    dumped = data.model_dump(mode="json")
    result = ServiceResult(
        data=dumped,
        source_url="internal://runtime",
        found=True,
        cached=False,
        fetched_at=now,
        cache_expires_at=now + timedelta(seconds=5),
        fingerprint=fingerprint(dumped),
    )
    return envelope_response(request, result)


@app.get("/latest", response_model=Envelope[ListingData], tags=["Search"], dependencies=business_dependencies)
async def latest(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=75),
    filter_mode: FilterMode = Query(default=FilterMode.all, alias="filter"),
    media_type: PublicMediaType = Query(default=PublicMediaType.all),
    sort: SortMode = Query(default=SortMode.date),
    order: SortOrder = Query(default=SortOrder.desc),
):
    result = await request.app.state.service.listing(
        query=None,
        page=page,
        limit=limit,
        filter_mode=filter_mode,
        media_type=media_type,
        sort=sort,
        order=order,
    )
    return envelope_response(request, result)


@app.get("/search", response_model=Envelope[ListingData], tags=["Search"], dependencies=business_dependencies)
async def search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=75),
    filter_mode: FilterMode = Query(default=FilterMode.all, alias="filter"),
    media_type: PublicMediaType = Query(default=PublicMediaType.all),
    sort: SortMode = Query(default=SortMode.date),
    order: SortOrder = Query(default=SortOrder.desc),
    include_details: bool = Query(default=False),
):
    result = await request.app.state.service.listing(
        query=q,
        page=page,
        limit=limit,
        filter_mode=filter_mode,
        media_type=media_type,
        sort=sort,
        order=order,
        include_details=include_details,
    )
    return envelope_response(request, result)


@app.get("/search/resolve", response_model=Envelope[ResolveData], tags=["Search"], dependencies=business_dependencies)
async def resolve(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=25),
    filter_mode: FilterMode = Query(default=FilterMode.all, alias="filter"),
    media_type: PublicMediaType = Query(default=PublicMediaType.all),
    include_details: bool = Query(default=True),
):
    result = await request.app.state.service.resolve(
        query=q,
        limit=limit,
        filter_mode=filter_mode,
        media_type=media_type,
        include_details=include_details,
    )
    return envelope_response(request, result)


def detail_parameters(
    include_description: bool = Query(default=True),
    include_raw: bool = Query(default=False),
    include_files: bool = Query(default=True),
    files_offset: int = Query(default=0, ge=0),
    files_limit: int = Query(default=200, ge=1, le=1000),
):
    return include_description, include_raw, include_files, files_offset, files_limit


@app.get("/torrents/by-hash/{info_hash}", response_model=Envelope[TorrentDetail], tags=["Torrents"], dependencies=business_dependencies)
async def torrent_by_hash(request: Request, info_hash: str, params=Depends(detail_parameters)):
    result = await request.app.state.service.get_by_hash(
        info_hash=info_hash,
        include_description=params[0],
        include_raw=params[1],
        include_files=params[2],
        files_offset=params[3],
        files_limit=params[4],
    )
    return envelope_response(request, result)


@app.get("/torrents/{torrent_id}", response_model=Envelope[TorrentDetail], tags=["Torrents"], dependencies=business_dependencies)
async def torrent_detail(request: Request, torrent_id: int, params=Depends(detail_parameters)):
    if torrent_id < 1:
        raise InvalidParameter("Torrent id must be positive.")
    result = await request.app.state.service.get_detail(
        torrent_id=torrent_id,
        include_description=params[0],
        include_raw=params[1],
        include_files=params[2],
        files_offset=params[3],
        files_limit=params[4],
    )
    return envelope_response(request, result)
