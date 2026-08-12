from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from app.config import NYAA_CATEGORY_ID, Settings
from app.exceptions import InvalidParameter
from app.models import FilterMode, SortMode, SortOrder

FILTER_MAP = {
    FilterMode.all: "0",
    FilterMode.no_remakes: "1",
    FilterMode.trusted: "2",
}

SORT_MAP = {
    SortMode.date: "id",
    SortMode.seeders: "seeders",
    SortMode.leechers: "leechers",
    SortMode.downloads: "downloads",
    SortMode.size: "size",
    SortMode.comments: "comments",
}

ORDER_MAP = {SortOrder.asc: "asc", SortOrder.desc: "desc"}


class QueryBuilder:
    def __init__(self, settings: Settings):
        self.base_url = settings.nyaa_base_url.rstrip("/")
        self.category_id = settings.nyaa_category_id

    def build_rss(
        self,
        *,
        query: str | None,
        page: int,
        filter_mode: FilterMode,
        sort: SortMode,
        order: SortOrder,
    ) -> str:
        if self.category_id != NYAA_CATEGORY_ID:
            raise InvalidParameter("Nyaa category is not the locked English literature category.")
        params: list[tuple[str, str]] = [
            ("page", "rss"),
            ("q", (query or "").strip()),
            ("f", FILTER_MAP[filter_mode]),
            ("c", NYAA_CATEGORY_ID),
            ("p", str(max(1, page))),
            ("s", SORT_MAP[sort]),
            ("o", ORDER_MAP[order]),
        ]
        url = f"{self.base_url}/?{urlencode(params)}"
        self.validate_scoped_search_url(url)
        return url

    def build_hash_search(self, info_hash: str) -> str:
        params = [("q", info_hash), ("f", "0"), ("c", NYAA_CATEGORY_ID)]
        url = f"{self.base_url}/?{urlencode(params)}"
        self.validate_scoped_search_url(url)
        return url

    def build_detail(self, torrent_id: int) -> str:
        if torrent_id < 1:
            raise InvalidParameter("Torrent id must be positive.")
        return urljoin(f"{self.base_url}/", f"view/{torrent_id}")

    def build_torrent_download(self, torrent_id: int) -> str:
        return urljoin(f"{self.base_url}/", f"download/{torrent_id}.torrent")

    def validate_scoped_search_url(self, url: str) -> None:
        parsed = urlparse(url)
        base = urlparse(self.base_url)
        if parsed.scheme != base.scheme or parsed.hostname != base.hostname:
            raise InvalidParameter("Upstream URL host is outside the configured Nyaa host.")
        values = parse_qs(parsed.query, keep_blank_values=True)
        if values.get("c") != [NYAA_CATEGORY_ID]:
            raise InvalidParameter("Every Nyaa search must contain exactly c=3_1.")

    def is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        base = urlparse(self.base_url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == base.hostname
