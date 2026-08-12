from __future__ import annotations

import pytest

from app.exceptions import UpstreamParseError
from app.models import MediaType, TorrentFile
from app.nyaa.classifier import MediaClassifier
from app.nyaa.parser import parse_detail_html, parse_rss, parse_size_bytes


def test_parse_rss_exposes_torrent_fields(sample_rss):
    classifier = MediaClassifier()
    items = parse_rss(sample_rss, base_url="https://nyaa.si", trackers=["udp://tracker.example"], classifier=classifier)
    assert len(items) == 4
    first = items[0]
    assert first.id == 1234567
    assert first.category_id == "3_1"
    assert first.info_hash == "0123456789ABCDEF0123456789ABCDEF01234567"
    assert first.size_bytes == 327_575_142
    assert first.seeders == 12
    assert first.trusted is True
    assert first.magnet_url.startswith("magnet:?xt=urn:btih:")
    assert first.media_type == MediaType.manga


def test_parse_invalid_rss_raises_typed_error():
    with pytest.raises(UpstreamParseError):
        parse_rss("<rss>", base_url="https://nyaa.si", trackers=[], classifier=MediaClassifier())


def test_parse_size_supports_binary_and_decimal_units():
    assert parse_size_bytes("1 MiB") == 1_048_576
    assert parse_size_bytes("1 MB") == 1_000_000
    assert parse_size_bytes(None) is None


def test_parse_detail_sanitizes_html_and_files(sample_detail):
    parsed = parse_detail_html(sample_detail, base_url="https://nyaa.si")
    assert parsed["category_id"] == "3_1"
    assert parsed["uploader"] == "example-group"
    assert parsed["info_hash"] == "0123456789ABCDEF0123456789ABCDEF01234567"
    assert len(parsed["files"]) == 3
    assert "<script" not in parsed["description_html"]
    assert "javascript:" not in parsed["description_html"]


def test_classifier_uses_file_evidence():
    classifier = MediaClassifier()
    media_type, confidence, signals = classifier.classify(
        "Ambiguous Release Vol. 1",
        files=[TorrentFile(path=f"page-{index:03}.jpg") for index in range(20)],
    )
    assert media_type == MediaType.manga
    assert confidence >= 0.75
    assert "detail_image_majority" in signals


def test_classifier_distinguishes_light_novel_and_magazine():
    classifier = MediaClassifier()
    light_novel = classifier.classify("Quiet Hero Vol. 4 [Light Novel]", files=[TorrentFile(path="book.epub")])
    magazine = classifier.classify("Weekly Comic Magazine 2026 Issue 30")
    assert light_novel[0] == MediaType.light_novel
    assert magazine[0] == MediaType.magazine


def test_pdf_alone_stays_unknown():
    result = MediaClassifier().classify("Ambiguous Book", files=[TorrentFile(path="book.pdf")])
    assert result[0] == MediaType.unknown
