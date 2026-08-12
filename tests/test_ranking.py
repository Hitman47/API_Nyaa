from __future__ import annotations

from datetime import UTC, datetime

from app.models import MediaType, PublicMediaType, ResolveConfidence, TorrentSummary
from app.nyaa.ranking import rank_results


def make_item(item_id: int, title: str, *, seeders: int, trusted: bool, media_type: MediaType) -> TorrentSummary:
    return TorrentSummary(
        id=item_id,
        title=title,
        details_url=f"https://nyaa.si/view/{item_id}",
        published_at=datetime.now(UTC),
        size="10 MiB",
        size_bytes=10_485_760,
        seeders=seeders,
        info_hash=f"{item_id:040X}"[-40:],
        magnet_url=f"magnet:?xt=urn:btih:{item_id:040X}",
        torrent_url=f"https://nyaa.si/download/{item_id}.torrent",
        trusted=trusted,
        category_id="3_1",
        category_name="Literature - English-translated",
        media_type=media_type,
        media_type_confidence=0.9,
    )


def test_relevance_beats_raw_seeder_count():
    exact = make_item(1, "Rare Hero Vol. 3 [Manga]", seeders=2, trusted=True, media_type=MediaType.manga)
    popular = make_item(2, "Completely Different Series", seeders=5000, trusted=True, media_type=MediaType.manga)
    ranked, confidence = rank_results("Rare Hero Vol 3", [popular, exact], PublicMediaType.manga)
    assert ranked[0].id == 1
    assert confidence in {ResolveConfidence.high, ResolveConfidence.medium}


def test_low_relevance_has_no_best_confidence():
    item = make_item(3, "Unrelated Dictionary", seeders=0, trusted=False, media_type=MediaType.novel)
    _, confidence = rank_results("Rare Hero", [item], PublicMediaType.manga)
    assert confidence == ResolveConfidence.none
