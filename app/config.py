from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION_DATA_HARD_LIMIT = 350_000_000
NYAA_CATEGORY_ID = "3_1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(default="API_Nyaa", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="text", alias="LOG_FORMAT")
    enable_docs: bool = Field(default=True, alias="ENABLE_DOCS")
    api_token: SecretStr = Field(default=SecretStr(""), alias="API_TOKEN")

    nyaa_base_url: str = Field(default="https://nyaa.si", alias="NYAA_BASE_URL")
    nyaa_category_id: str = Field(default=NYAA_CATEGORY_ID, alias="NYAA_CATEGORY_ID")
    user_agent: str = Field(default="API_Nyaa/0.1 (+private-selfhosted)", alias="USER_AGENT")
    request_timeout_seconds: float = Field(default=20.0, alias="REQUEST_TIMEOUT_SECONDS", gt=0, le=60)
    request_max_retries: int = Field(default=2, alias="REQUEST_MAX_RETRIES", ge=0, le=4)
    upstream_requests_per_second: float = Field(default=1.0, alias="UPSTREAM_REQUESTS_PER_SECOND", gt=0, le=5)
    upstream_max_concurrency: int = Field(default=2, alias="UPSTREAM_MAX_CONCURRENCY", ge=1, le=4)

    db_path: Path = Field(default=Path("/data/cache.sqlite3"), alias="DB_PATH")
    data_hard_limit_bytes: int = Field(default=PRODUCTION_DATA_HARD_LIMIT, alias="DATA_HARD_LIMIT_BYTES", ge=1_000_000)
    cache_db_target_bytes: int = Field(default=256_000_000, alias="CACHE_DB_TARGET_BYTES", ge=1_000_000)
    sqlite_wal_hard_limit_bytes: int = Field(default=32_000_000, alias="SQLITE_WAL_HARD_LIMIT_BYTES", ge=1_000_000)
    max_cache_entry_bytes: int = Field(default=5_000_000, alias="MAX_CACHE_ENTRY_BYTES", ge=10_000)
    sqlite_busy_timeout_ms: int = Field(default=5_000, alias="SQLITE_BUSY_TIMEOUT_MS", ge=100, le=60_000)
    cache_memory_entries: int = Field(default=512, alias="CACHE_MEMORY_ENTRIES", ge=0, le=10_000)
    debug_capture_html_on_error: bool = Field(default=False, alias="DEBUG_CAPTURE_HTML_ON_ERROR")
    debug_max_bytes: int = Field(default=50_000_000, alias="DEBUG_MAX_BYTES", ge=0)
    cache_ttl_search_seconds: int = Field(default=300, alias="CACHE_TTL_SEARCH_SECONDS", ge=1)
    cache_ttl_detail_seconds: int = Field(default=21_600, alias="CACHE_TTL_DETAIL_SECONDS", ge=1)
    cache_stale_grace_seconds: int = Field(default=604_800, alias="CACHE_STALE_GRACE_SECONDS", ge=0)
    negative_cache_ttl_seconds: int = Field(default=120, alias="NEGATIVE_CACHE_TTL_SECONDS", ge=0)

    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(default=60, alias="RATE_LIMIT_REQUESTS", ge=1, le=10_000)
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS", ge=1, le=3_600)
    default_limit: int = Field(default=25, alias="DEFAULT_LIMIT", ge=1, le=75)
    max_limit: int = Field(default=75, alias="MAX_LIMIT", ge=1, le=75)
    max_detail_enrichments: int = Field(default=10, alias="MAX_DETAIL_ENRICHMENTS", ge=0, le=10)

    magnet_trackers: str = Field(
        default="udp://tracker.opentrackr.org:1337/announce,udp://open.stealth.si:80/announce",
        alias="MAGNET_TRACKERS",
    )

    @field_validator("nyaa_category_id")
    @classmethod
    def category_must_be_locked(cls, value: str) -> str:
        if value != NYAA_CATEGORY_ID:
            raise ValueError("NYAA_CATEGORY_ID is locked to 3_1")
        return value

    @field_validator("log_format")
    @classmethod
    def log_format_must_be_supported(cls, value: str) -> str:
        normalized = value.casefold()
        if normalized not in {"text", "json"}:
            raise ValueError("LOG_FORMAT must be text or json")
        return normalized

    @field_validator("nyaa_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("NYAA_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("NYAA_BASE_URL cannot contain a query or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_production_invariants(self) -> Settings:
        if self.cache_db_target_bytes >= self.data_hard_limit_bytes:
            raise ValueError("CACHE_DB_TARGET_BYTES must be below DATA_HARD_LIMIT_BYTES")
        reserve = self.data_hard_limit_bytes - (
            self.cache_db_target_bytes + self.sqlite_wal_hard_limit_bytes + self.debug_max_bytes
        )
        if reserve < 10_000_000:
            raise ValueError("storage budgets must leave at least 10 MB of safety reserve")
        if self.max_cache_entry_bytes >= self.sqlite_wal_hard_limit_bytes:
            raise ValueError("MAX_CACHE_ENTRY_BYTES must be below SQLITE_WAL_HARD_LIMIT_BYTES")
        if self.app_env.lower() == "production":
            if self.data_hard_limit_bytes > PRODUCTION_DATA_HARD_LIMIT:
                raise ValueError("production /data limit cannot exceed 350000000 bytes")
            if urlparse(self.nyaa_base_url).hostname not in {"nyaa.si"}:
                raise ValueError("production NYAA_BASE_URL host must be nyaa.si")
        return self

    @property
    def docs_url(self) -> str | None:
        return "/docs" if self.enable_docs else None

    @property
    def redoc_url(self) -> str | None:
        return "/redoc" if self.enable_docs else None

    @property
    def openapi_url(self) -> str | None:
        return "/openapi.json" if self.enable_docs else None

    @property
    def api_token_value(self) -> str:
        return self.api_token.get_secret_value()

    @property
    def allowed_upstream_hosts(self) -> set[str]:
        hostname = urlparse(self.nyaa_base_url).hostname
        return {hostname.lower()} if hostname else set()

    @property
    def tracker_list(self) -> list[str]:
        return [part.strip() for part in self.magnet_trackers.split(",") if part.strip()][:5]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
