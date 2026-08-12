from __future__ import annotations

import math
import re
import unicodedata
from datetime import UTC, datetime

from rapidfuzz import fuzz

from app.models import PublicMediaType, ResolveConfidence, TorrentSummary

RANKING_VERSION = "1.0"


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", value))


def rank_candidate(query: str, item: TorrentSummary, requested: PublicMediaType) -> float:
    normalized_query = _normalize(query)
    normalized_title = _normalize(item.title)
    similarity = fuzz.token_set_ratio(normalized_query, normalized_title) / 100
    score = similarity * 55
    if normalized_query and normalized_query in normalized_title:
        score += 15
    if requested != PublicMediaType.all:
        if item.media_type.value == requested.value:
            score += 10
        elif item.media_type.value != "unknown":
            score -= 15
    if item.trusted:
        score += 8
    if item.remake:
        score -= 8
    score += min(5.0, math.log10(max(0, item.seeders) + 1) * 2.5)
    now = datetime.now(UTC)
    published = item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - published).total_seconds() / 86_400)
    score += max(0.0, 4.0 - min(4.0, age_days / 90))
    score += min(3.0, item.media_type_confidence * 3)
    return round(max(0.0, min(100.0, score)), 3)


def rank_results(
    query: str,
    items: list[TorrentSummary],
    requested: PublicMediaType,
) -> tuple[list[TorrentSummary], ResolveConfidence]:
    ranked: list[TorrentSummary] = []
    for item in items:
        ranked.append(item.model_copy(update={"rank_score": rank_candidate(query, item, requested)}))
    ranked.sort(key=lambda item: (item.rank_score or 0, item.seeders, item.published_at), reverse=True)
    if not ranked or (ranked[0].rank_score or 0) < 50:
        return ranked, ResolveConfidence.none
    best = ranked[0].rank_score or 0
    second = ranked[1].rank_score if len(ranked) > 1 and ranked[1].rank_score is not None else 0
    if best >= 80 and best - second >= 10:
        return ranked, ResolveConfidence.high
    if best >= 65:
        return ranked, ResolveConfidence.medium
    return ranked, ResolveConfidence.low
