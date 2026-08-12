from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilterMode(StrEnum):
    all = "all"
    no_remakes = "no_remakes"
    trusted = "trusted"


class MediaType(StrEnum):
    all = "all"
    manga = "manga"
    light_novel = "light_novel"
    novel = "novel"
    artbook = "artbook"
    magazine = "magazine"
    unknown = "unknown"


class PublicMediaType(StrEnum):
    all = "all"
    manga = "manga"
    light_novel = "light_novel"
    novel = "novel"
    artbook = "artbook"
    unknown = "unknown"


class SortMode(StrEnum):
    date = "date"
    seeders = "seeders"
    leechers = "leechers"
    downloads = "downloads"
    size = "size"
    comments = "comments"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


class ResolveConfidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


class WarningItem(BaseModel):
    code: str
    detail: str


class TorrentFile(BaseModel):
    path: str
    size: str | None = None
    size_bytes: int | None = None


class FilePage(BaseModel):
    total: int
    offset: int
    limit: int
    has_more: bool
    items: list[TorrentFile]


class TorrentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: str
    details_url: str
    published_at: datetime
    size: str
    size_bytes: int | None = None
    seeders: int = 0
    leechers: int = 0
    downloads: int = 0
    comments: int = 0
    info_hash: str
    magnet_url: str
    torrent_url: str | None = None
    trusted: bool = False
    remake: bool = False
    category_id: str
    category_name: str
    media_type: MediaType = MediaType.unknown
    media_type_confidence: float = Field(default=0.0, ge=0, le=1)
    classification_signals: list[str] = Field(default_factory=list)
    rank_score: float | None = Field(default=None, ge=0, le=100)

    @field_validator("info_hash")
    @classmethod
    def normalize_info_hash(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 40 or any(ch not in "0123456789ABCDEF" for ch in value):
            raise ValueError("info_hash must contain exactly 40 hexadecimal characters")
        return value


class TorrentDetail(TorrentSummary):
    uploader: str | None = None
    information_url: str | None = None
    description_text: str | None = None
    description_html: str | None = None
    files: FilePage | None = None


class ListingData(BaseModel):
    query: str | None = None
    page: int
    limit: int
    has_more: bool
    filter: FilterMode
    media_type: PublicMediaType
    sort: SortMode
    order: SortOrder
    items: list[TorrentSummary]


class ResolveData(BaseModel):
    query: str
    media_type_requested: PublicMediaType
    confidence: ResolveConfidence
    best: TorrentSummary | None
    candidates: list[TorrentSummary]
    ranking_version: str = "1.0"


class RuntimeData(BaseModel):
    ok: bool = True
    uptime_seconds: float
    cache: dict[str, Any]
    storage: dict[str, Any]
    rate_limit: dict[str, Any]
    upstream: dict[str, Any]
    metrics: dict[str, Any]
    defaults: dict[str, Any]


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    schema_version: str = "1.0"
    ok: bool = True
    found: bool = True
    source: str = "nyaa"
    source_url: str
    cached: bool = False
    fetched_at: datetime
    cache_expires_at: datetime
    partial: bool = False
    warnings: list[WarningItem] = Field(default_factory=list)
    fingerprint: str
    data: T


class ErrorResponse(BaseModel):
    code: str
    detail: str
    request_id: str
