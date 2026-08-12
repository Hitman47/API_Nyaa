from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.cache import CacheRecord, SQLiteCache
from app.config import NYAA_CATEGORY_ID, Settings
from app.exceptions import (
    APIError,
    InvalidQuery,
    OutOfScopeResource,
    ResourceNotFound,
    UpstreamParseError,
)
from app.http import AsyncFetcher, FetchResponse
from app.metrics import MetricsStore
from app.models import (
    FilePage,
    FilterMode,
    ListingData,
    MediaType,
    PublicMediaType,
    ResolveData,
    RuntimeData,
    SortMode,
    SortOrder,
    TorrentDetail,
    TorrentSummary,
    WarningItem,
)
from app.nyaa.classifier import CLASSIFIER_VERSION, MediaClassifier
from app.nyaa.parser import build_magnet, find_torrent_id_in_html, parse_detail_html, parse_rss, parse_size_bytes
from app.nyaa.query import QueryBuilder
from app.nyaa.ranking import RANKING_VERSION, rank_results

Parser = Callable[[FetchResponse], Any]


@dataclass(slots=True)
class ServiceResult:
    data: Any
    source_url: str
    found: bool
    cached: bool
    fetched_at: datetime
    cache_expires_at: datetime
    partial: bool = False
    warnings: list[WarningItem] = field(default_factory=list)
    fingerprint: str = ""


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class NyaaService:
    def __init__(
        self,
        *,
        settings: Settings,
        fetcher: AsyncFetcher,
        cache: SQLiteCache,
        metrics: MetricsStore,
    ):
        self.settings = settings
        self.fetcher = fetcher
        self.cache = cache
        self.metrics = metrics
        self.query_builder = QueryBuilder(settings)
        self.classifier = MediaClassifier()

    @staticmethod
    def _from_cache(record: CacheRecord, *, cached: bool = True, partial: bool = False, warning: WarningItem | None = None) -> ServiceResult:
        payload = record.payload
        warnings = [WarningItem.model_validate(item) for item in payload.get("warnings", [])]
        if warning:
            warnings.append(warning)
        return ServiceResult(
            data=payload["data"],
            source_url=payload["source_url"],
            found=payload.get("found", True),
            cached=cached,
            fetched_at=record.fetched_at,
            cache_expires_at=record.expires_at,
            partial=partial,
            warnings=warnings,
            fingerprint=record.fingerprint,
        )

    @staticmethod
    def _raise_negative(code: str, detail: str) -> None:
        if code == "RESOURCE_NOT_FOUND":
            raise ResourceNotFound(detail)
        if code == "OUT_OF_SCOPE_RESOURCE":
            raise OutOfScopeResource(detail)
        raise UpstreamParseError(detail)

    async def _cacheable(
        self,
        *,
        key: str,
        kind: str,
        url: str,
        ttl_seconds: int,
        parser: Parser,
    ) -> ServiceResult:
        cached = self.cache.get(key)
        if cached and cached.fresh:
            self.metrics.increment("service_cache_fresh")
            return self._from_cache(cached)

        negative = self.cache.get_negative(key)
        if negative:
            if cached and cached.stale_usable:
                return self._from_cache(
                    cached,
                    partial=True,
                    warning=WarningItem(code=negative.code, detail="Stale cache served after a repeated upstream error."),
                )
            self._raise_negative(negative.code, negative.detail)

        response: FetchResponse | None = None
        try:
            response = await self.fetcher.get(url)
            with self.metrics.measure("upstream_parse"):
                data = parser(response)
        except APIError as exc:
            if (
                isinstance(exc, UpstreamParseError)
                and response is not None
                and self.settings.debug_capture_html_on_error
            ):
                self.cache.capture_diagnostic(
                    "upstream-parse",
                    response.text,
                    max_total_bytes=self.settings.debug_max_bytes,
                )
            if isinstance(exc, (ResourceNotFound, OutOfScopeResource, UpstreamParseError)):
                self.cache.set_negative(
                    key,
                    code=exc.code,
                    detail=exc.detail,
                    ttl_seconds=60 if isinstance(exc, UpstreamParseError) else self.settings.negative_cache_ttl_seconds,
                )
            if cached and cached.stale_usable:
                self.metrics.increment("service_stale_served")
                return self._from_cache(
                    cached,
                    partial=True,
                    warning=WarningItem(code=exc.code, detail="Stale cache served because Nyaa could not be refreshed."),
                )
            raise

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        value_fingerprint = fingerprint(data)
        payload = {"source_url": response.url, "found": bool(data), "data": data, "warnings": []}
        written = self.cache.set(
            key,
            kind=kind,
            payload=payload,
            fingerprint=value_fingerprint,
            fetched_at=now,
            expires_at=expires_at,
        )
        warnings: list[WarningItem] = []
        if not written:
            warnings.append(
                WarningItem(code="CACHE_WRITE_SKIPPED", detail="The response is valid but was not persisted because of cache limits.")
            )
        return ServiceResult(
            data=data,
            source_url=response.url,
            found=bool(data),
            cached=False,
            fetched_at=now,
            cache_expires_at=expires_at,
            warnings=warnings,
            fingerprint=value_fingerprint,
        )

    def _parse_listing(self, response: FetchResponse) -> dict[str, Any]:
        parsed = parse_rss(
            response.text,
            base_url=self.settings.nyaa_base_url,
            trackers=self.settings.tracker_list,
            classifier=self.classifier,
        )
        valid: list[dict[str, Any]] = []
        rejected = 0
        for item in parsed:
            if item.category_id != NYAA_CATEGORY_ID:
                rejected += 1
                self.metrics.increment("category_mismatch_rejected")
                continue
            valid.append(item.model_dump(mode="json"))
        return {"items": valid, "rejected_count": rejected}

    @staticmethod
    def _filter_media(items: list[TorrentSummary], requested: PublicMediaType) -> list[TorrentSummary]:
        if requested == PublicMediaType.all:
            return [item for item in items if item.media_type != MediaType.magazine]
        exact = [item for item in items if item.media_type.value == requested.value]
        unknown = [item for item in items if item.media_type == MediaType.unknown]
        return exact + unknown

    @staticmethod
    def _sort_items(items: list[TorrentSummary], sort: SortMode, order: SortOrder) -> list[TorrentSummary]:
        key_map: dict[SortMode, Callable[[TorrentSummary], Any]] = {
            SortMode.date: lambda item: item.published_at,
            SortMode.seeders: lambda item: item.seeders,
            SortMode.leechers: lambda item: item.leechers,
            SortMode.downloads: lambda item: item.downloads,
            SortMode.size: lambda item: item.size_bytes or 0,
            SortMode.comments: lambda item: item.comments,
        }
        return sorted(items, key=key_map[sort], reverse=order == SortOrder.desc)

    def _derived_result(self, source: ServiceResult, data: Any, warnings: list[WarningItem] | None = None) -> ServiceResult:
        dumped = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        if isinstance(dumped, dict) and "items" in dumped:
            found = bool(dumped["items"])
        elif isinstance(dumped, dict) and "best" in dumped:
            found = dumped["best"] is not None
        else:
            found = bool(dumped)
        return ServiceResult(
            data=dumped,
            source_url=source.source_url,
            found=found,
            cached=source.cached,
            fetched_at=source.fetched_at,
            cache_expires_at=source.cache_expires_at,
            partial=source.partial,
            warnings=[*source.warnings, *(warnings or [])],
            fingerprint=fingerprint(dumped),
        )

    async def listing(
        self,
        *,
        query: str | None,
        page: int,
        limit: int,
        filter_mode: FilterMode,
        media_type: PublicMediaType,
        sort: SortMode,
        order: SortOrder,
        include_details: bool = False,
        allow_fallback: bool = True,
    ) -> ServiceResult:
        normalized_query = (query or "").strip()
        if query is not None and not normalized_query:
            raise InvalidQuery("Search query cannot be blank.")
        url = self.query_builder.build_rss(
            query=normalized_query,
            page=page,
            filter_mode=filter_mode,
            sort=sort,
            order=order,
        )
        cache_key = f"rss:v2:{url}"
        source = await self._cacheable(
            key=cache_key,
            kind="search" if query is not None else "latest",
            url=url,
            ttl_seconds=self.settings.cache_ttl_search_seconds,
            parser=self._parse_listing,
        )
        raw_items = [TorrentSummary.model_validate(item) for item in source.data.get("items", [])]
        if filter_mode == FilterMode.no_remakes:
            raw_items = [item for item in raw_items if not item.remake]
        elif filter_mode == FilterMode.trusted:
            raw_items = [item for item in raw_items if item.trusted]
        filtered = self._filter_media(raw_items, media_type)
        warnings: list[WarningItem] = []
        rejected_count = int(source.data.get("rejected_count", 0))
        if rejected_count:
            warnings.append(
                WarningItem(
                    code="CATEGORY_MISMATCH_REJECTED",
                    detail=f"{rejected_count} upstream item(s) outside c=3_1 were rejected.",
                )
            )

        if normalized_query and not filtered and allow_fallback and media_type != PublicMediaType.all:
            hint = {
                PublicMediaType.manga: "manga",
                PublicMediaType.light_novel: '"light novel"',
                PublicMediaType.novel: "epub",
                PublicMediaType.artbook: "artbook",
                PublicMediaType.unknown: "",
            }.get(media_type, "")
            if hint:
                fallback = await self.listing(
                    query=f"{normalized_query} {hint}",
                    page=page,
                    limit=limit,
                    filter_mode=filter_mode,
                    media_type=media_type,
                    sort=sort,
                    order=order,
                    include_details=include_details,
                    allow_fallback=False,
                )
                fallback_data = ListingData.model_validate(fallback.data)
                fallback_data.query = normalized_query
                warnings.append(WarningItem(code="SEARCH_FALLBACK_USED", detail="One media-specific fallback search was used."))
                return self._derived_result(fallback, fallback_data, warnings)

        if include_details:
            enriched: list[TorrentSummary] = []
            enrich_count = min(len(filtered), limit, self.settings.max_detail_enrichments)
            for index, item in enumerate(filtered):
                if index >= enrich_count:
                    enriched.append(item)
                    continue
                try:
                    detail_result = await self.get_detail(
                        torrent_id=item.id,
                        include_description=True,
                        include_raw=False,
                        include_files=True,
                        files_offset=0,
                        files_limit=200,
                    )
                    detail = TorrentDetail.model_validate(detail_result.data)
                    enriched.append(
                        item.model_copy(
                            update={
                                "media_type": detail.media_type,
                                "media_type_confidence": detail.media_type_confidence,
                                "classification_signals": detail.classification_signals,
                            }
                        )
                    )
                except APIError as exc:
                    enriched.append(item)
                    warnings.append(WarningItem(code="DETAIL_ENRICHMENT_FAILED", detail=f"Detail enrichment failed for torrent {item.id}: {exc.code}."))
            filtered = enriched

        filtered = self._sort_items(filtered, sort, order)
        has_more = len(filtered) > limit or len(raw_items) >= self.settings.max_limit
        page_items = filtered[:limit]
        data = ListingData(
            query=normalized_query or None,
            page=page,
            limit=limit,
            has_more=has_more,
            filter=filter_mode,
            media_type=media_type,
            sort=sort,
            order=order,
            items=page_items,
        )
        return self._derived_result(source, data, warnings)

    async def resolve(
        self,
        *,
        query: str,
        limit: int,
        filter_mode: FilterMode,
        media_type: PublicMediaType,
        include_details: bool,
    ) -> ServiceResult:
        source = await self.listing(
            query=query,
            page=1,
            limit=min(limit, 25),
            filter_mode=filter_mode,
            media_type=media_type,
            sort=SortMode.date,
            order=SortOrder.desc,
            include_details=include_details,
        )
        listing = ListingData.model_validate(source.data)
        ranked, confidence = rank_results(query, listing.items, media_type)
        best = ranked[0] if ranked and confidence.value != "none" else None
        data = ResolveData(
            query=query.strip(),
            media_type_requested=media_type,
            confidence=confidence,
            best=best,
            candidates=ranked,
            ranking_version=RANKING_VERSION,
        )
        return self._derived_result(source, data)

    def _parse_detail(self, response: FetchResponse, torrent_id: int) -> dict[str, Any]:
        parsed = parse_detail_html(response.text, base_url=self.settings.nyaa_base_url)
        if parsed.get("category_id") != NYAA_CATEGORY_ID:
            raise OutOfScopeResource("Torrent is not part of Literature - English-translated (c=3_1).")
        required = ["title", "info_hash", "published_at", "size"]
        missing = [name for name in required if not parsed.get(name)]
        if missing:
            raise UpstreamParseError(f"Unable to extract required torrent fields: {', '.join(missing)}.")
        files = parsed.get("files") or []
        media_type, confidence, signals = self.classifier.classify(
            str(parsed["title"]),
            files=files,
            description=parsed.get("description_text") if isinstance(parsed.get("description_text"), str) else None,
        )
        info_hash = str(parsed["info_hash"])
        detail = TorrentDetail(
            id=torrent_id,
            title=str(parsed["title"]),
            details_url=response.url,
            published_at=parsed["published_at"],
            size=str(parsed["size"]),
            size_bytes=parse_size_bytes(str(parsed["size"])),
            seeders=int(parsed.get("seeders") or 0),
            leechers=int(parsed.get("leechers") or 0),
            downloads=int(parsed.get("downloads") or 0),
            comments=int(parsed.get("comments") or 0),
            info_hash=info_hash,
            magnet_url=str(parsed.get("magnet_url") or build_magnet(info_hash, str(parsed["title"]), self.settings.tracker_list)),
            torrent_url=str(parsed.get("torrent_url") or self.query_builder.build_torrent_download(torrent_id)),
            trusted=bool(parsed.get("trusted")),
            remake=bool(parsed.get("remake")),
            category_id=NYAA_CATEGORY_ID,
            category_name=str(parsed.get("category_name") or "Literature - English-translated"),
            media_type=media_type,
            media_type_confidence=confidence,
            classification_signals=signals,
            uploader=parsed.get("uploader"),
            information_url=parsed.get("information_url"),
            description_text=parsed.get("description_text"),
            description_html=parsed.get("description_html"),
            files=FilePage(total=len(files), offset=0, limit=len(files), has_more=False, items=files),
        )
        return detail.model_dump(mode="json")

    async def get_detail(
        self,
        *,
        torrent_id: int,
        include_description: bool,
        include_raw: bool,
        include_files: bool,
        files_offset: int,
        files_limit: int,
    ) -> ServiceResult:
        url = self.query_builder.build_detail(torrent_id)
        source = await self._cacheable(
            key=f"detail:v2:classifier-{CLASSIFIER_VERSION}:{torrent_id}",
            kind="detail",
            url=url,
            ttl_seconds=self.settings.cache_ttl_detail_seconds,
            parser=lambda response: self._parse_detail(response, torrent_id),
        )
        detail = TorrentDetail.model_validate(source.data)
        if not include_description:
            detail.description_text = None
            detail.description_html = None
        elif not include_raw:
            detail.description_html = None
        if not include_files:
            detail.files = None
        elif detail.files:
            all_items = detail.files.items
            selected = all_items[files_offset : files_offset + files_limit]
            detail.files = FilePage(
                total=detail.files.total,
                offset=files_offset,
                limit=files_limit,
                has_more=files_offset + len(selected) < detail.files.total,
                items=selected,
            )
        return self._derived_result(source, detail)

    async def get_by_hash(
        self,
        *,
        info_hash: str,
        include_description: bool,
        include_raw: bool,
        include_files: bool,
        files_offset: int,
        files_limit: int,
    ) -> ServiceResult:
        normalized = info_hash.strip().upper()
        if not re.fullmatch(r"[A-F0-9]{40}", normalized):
            raise InvalidQuery("Info hash must contain exactly 40 hexadecimal characters.")
        response = await self.fetcher.get(self.query_builder.build_hash_search(normalized))
        match = re.search(r"/view/(\d+)", response.url)
        torrent_id = int(match.group(1)) if match else find_torrent_id_in_html(response.text)
        if not torrent_id:
            raise ResourceNotFound("No torrent with this info hash was found in c=3_1.")
        result = await self.get_detail(
            torrent_id=torrent_id,
            include_description=include_description,
            include_raw=include_raw,
            include_files=include_files,
            files_offset=files_offset,
            files_limit=files_limit,
        )
        detail = TorrentDetail.model_validate(result.data)
        if detail.info_hash != normalized:
            raise ResourceNotFound("No torrent with this info hash was found in c=3_1.")
        return result

    def runtime_data(self, rate_limit_snapshot: dict[str, Any]) -> RuntimeData:
        metrics = self.metrics.snapshot()
        cache = self.cache.snapshot()
        return RuntimeData(
            uptime_seconds=float(metrics["uptime_seconds"]),
            cache={key: value for key, value in cache.items() if key not in {"data_usage_bytes", "data_hard_limit_bytes", "usage_percent"}},
            storage={
                "data_usage_bytes": cache["data_usage_bytes"],
                "data_hard_limit_bytes": cache["data_hard_limit_bytes"],
                "usage_percent": cache["usage_percent"],
                "writes_enabled": cache["writes_enabled"],
                "db_size_bytes": cache["db_size_bytes"],
                "wal_size_bytes": cache["wal_size_bytes"],
            },
            rate_limit=rate_limit_snapshot,
            upstream=self.fetcher.snapshot(),
            metrics=metrics,
            defaults={
                "category_id": NYAA_CATEGORY_ID,
                "default_limit": self.settings.default_limit,
                "max_limit": self.settings.max_limit,
                "classifier_version": CLASSIFIER_VERSION,
                "ranking_version": RANKING_VERSION,
            },
        )
