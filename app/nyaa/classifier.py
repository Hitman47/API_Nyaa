from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import PurePosixPath

from app.models import MediaType, TorrentFile

CLASSIFIER_VERSION = "1.0"


class MediaClassifier:
    _patterns: dict[MediaType, list[tuple[re.Pattern[str], float, str]]] = {
        MediaType.manga: [
            (re.compile(r"\bmanga\b", re.I), 0.72, "title_manga_marker"),
            (re.compile(r"\b(?:ch(?:apter)?\.?\s*\d+)\b", re.I), 0.34, "title_chapter_marker"),
            (re.compile(r"\b(?:comic|manhwa|manhua)\b", re.I), 0.46, "title_comic_marker"),
        ],
        MediaType.light_novel: [
            (re.compile(r"\blight[ ._-]*novels?\b", re.I), 0.92, "title_light_novel_marker"),
            (re.compile(r"(?:^|[\s\[\]()._-])LN(?:$|[\s\[\]()._-])"), 0.62, "title_ln_marker"),
        ],
        MediaType.novel: [
            (re.compile(r"\bnovels?\b", re.I), 0.66, "title_novel_marker"),
            (re.compile(r"\b(?:ebook|prose)\b", re.I), 0.42, "title_ebook_marker"),
        ],
        MediaType.artbook: [
            (re.compile(r"\b(?:art[ ._-]*book|databook|guide[ ._-]*book|visual[ ._-]*guide)s?\b", re.I), 0.92, "title_artbook_marker"),
        ],
        MediaType.magazine: [
            (re.compile(r"\b(?:magazine|weekly|monthly)\b", re.I), 0.82, "title_magazine_marker"),
        ],
    }

    def classify(
        self,
        title: str,
        *,
        files: list[TorrentFile] | None = None,
        description: str | None = None,
    ) -> tuple[MediaType, float, list[str]]:
        normalized = unicodedata.normalize("NFKC", title)
        scores: Counter[MediaType] = Counter()
        signals: dict[MediaType, list[str]] = {kind: [] for kind in MediaType if kind not in {MediaType.all, MediaType.unknown}}

        haystacks = [(normalized, 1.0)]
        if description:
            haystacks.append((unicodedata.normalize("NFKC", description)[:20_000], 0.45))

        for media_type, patterns in self._patterns.items():
            for pattern, weight, signal in patterns:
                if any(pattern.search(text) for text, _ in haystacks):
                    multiplier = next(mult for text, mult in haystacks if pattern.search(text))
                    scores[media_type] += weight * multiplier
                    signals[media_type].append(signal)

        extensions: Counter[str] = Counter()
        if files:
            for item in files[:2_000]:
                suffix = PurePosixPath(item.path.replace("\\", "/")).suffix.lower()
                if suffix:
                    extensions[suffix] += 1
            total = sum(extensions.values())
            image_total = sum(extensions[ext] for ext in {".jpg", ".jpeg", ".png", ".webp", ".avif"})
            if extensions[".cbz"] or extensions[".cbr"]:
                scores[MediaType.manga] += 0.94
                signals[MediaType.manga].append("detail_comic_archive")
            if total >= 10 and image_total / total >= 0.8:
                scores[MediaType.manga] += 0.88
                signals[MediaType.manga].append("detail_image_majority")
            if extensions[".epub"] or extensions[".azw3"] or extensions[".mobi"]:
                target = MediaType.light_novel if scores[MediaType.light_novel] else MediaType.novel
                scores[target] += 0.72
                signals[target].append("detail_ebook_files")
            if extensions[".pdf"]:
                scores[MediaType.novel] += 0.12
                signals[MediaType.novel].append("detail_pdf_weak_signal")

        volume_marker = re.search(r"\b(?:vol(?:ume)?\.?|omnibus)\s*\d+", normalized, re.I)
        if volume_marker:
            for media_type in (MediaType.manga, MediaType.light_novel, MediaType.novel):
                scores[media_type] += 0.08
                signals[media_type].append("title_volume_marker")

        if not scores:
            return MediaType.unknown, 0.0, []
        ordered = scores.most_common()
        best_type, best_raw = ordered[0]
        second_raw = ordered[1][1] if len(ordered) > 1 else 0.0
        confidence = min(1.0, best_raw)
        if confidence < 0.55 or best_raw - second_raw < 0.12:
            return MediaType.unknown, round(confidence, 3), sorted(set(signals[best_type]))
        return best_type, round(confidence, 3), sorted(set(signals[best_type]))
