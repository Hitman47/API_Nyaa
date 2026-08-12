from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import bleach
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.exceptions import UpstreamParseError
from app.models import TorrentFile, TorrentSummary
from app.nyaa.classifier import MediaClassifier

SIZE_RE = re.compile(r"([\d.,]+)\s*([KMGTPE]?i?B)", re.I)
INFO_HASH_RE = re.compile(r"\b([A-Fa-f0-9]{40})\b")
ID_RE = re.compile(r"/(?:view|download)/(\d+)")


def parse_size_bytes(value: str | None) -> int | None:
    if not value:
        return None
    match = SIZE_RE.search(value.replace("\u00a0", " "))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    unit = match.group(2).upper()
    binary = "I" in unit
    power_letter = unit[0] if len(unit) > 1 else "B"
    powers = {"B": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}
    base = 1024 if binary else 1000
    return int(number * (base ** powers.get(power_letter, 0)))


def build_magnet(info_hash: str, title: str, trackers: list[str]) -> str:
    parts = [f"magnet:?xt=urn:btih:{info_hash.upper()}", f"dn={quote_plus(title)}"]
    parts.extend(f"tr={quote_plus(tracker)}" for tracker in trackers[:5])
    return "&".join(parts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(item: ET.Element, name: str, default: str = "") -> str:
    for child in item:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def _to_int(value: str) -> int:
    try:
        return max(0, int(value.strip()))
    except (TypeError, ValueError):
        return 0


def _parse_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UpstreamParseError("Unable to parse the torrent publication date.") from exc


def parse_rss(
    xml_text: str,
    *,
    base_url: str,
    trackers: list[str],
    classifier: MediaClassifier,
) -> list[TorrentSummary]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpstreamParseError("Nyaa returned invalid RSS XML.") from exc
    items: list[TorrentSummary] = []
    for element in root.iter():
        if _local_name(element.tag) != "item":
            continue
        title = _child_text(element, "title")
        details_url = _child_text(element, "guid")
        link = _child_text(element, "link")
        match_id = ID_RE.search(details_url or link)
        info_hash = _child_text(element, "infoHash").upper()
        category_id = _child_text(element, "categoryId")
        if not title or not match_id or not info_hash:
            continue
        torrent_id = int(match_id.group(1))
        media_type, confidence, signals = classifier.classify(title)
        torrent_url = link if link and not link.startswith("magnet:") else urljoin(f"{base_url}/", f"download/{torrent_id}.torrent")
        magnet_url = link if link.startswith("magnet:") else build_magnet(info_hash, title, trackers)
        size = _child_text(element, "size")
        try:
            summary = TorrentSummary(
                id=torrent_id,
                title=title,
                details_url=details_url or urljoin(f"{base_url}/", f"view/{torrent_id}"),
                published_at=_parse_date(_child_text(element, "pubDate")),
                size=size,
                size_bytes=parse_size_bytes(size),
                seeders=_to_int(_child_text(element, "seeders")),
                leechers=_to_int(_child_text(element, "leechers")),
                downloads=_to_int(_child_text(element, "downloads")),
                comments=_to_int(_child_text(element, "comments")),
                info_hash=info_hash,
                magnet_url=magnet_url,
                torrent_url=torrent_url,
                trusted=_child_text(element, "trusted").casefold() == "yes",
                remake=_child_text(element, "remake").casefold() == "yes",
                category_id=category_id,
                category_name=_child_text(element, "category"),
                media_type=media_type,
                media_type_confidence=confidence,
                classification_signals=signals,
            )
        except ValueError:
            continue
        items.append(summary)
    return items


def _extract_labeled_value(soup: BeautifulSoup, label: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$", re.I)
    node = soup.find(string=pattern)
    if not node:
        return None
    parent = node.parent
    if parent is None:
        return None
    row = parent.parent
    if row is not None:
        text = row.get_text(" ", strip=True)
        cleaned = re.sub(rf"^\s*{re.escape(label)}\s*:?\s*", "", text, flags=re.I)
        return cleaned or None
    return None


def parse_detail_html(html_text: str, *, base_url: str) -> dict[str, object]:
    soup = BeautifulSoup(html_text, "lxml")
    title_node = soup.select_one("h3.panel-title, h1, .torrent-name")
    title = title_node.get_text(" ", strip=True) if title_node else None
    category_id = None
    category_name = None
    for anchor in soup.select('a[href*="c="]'):
        query = parse_qs(urlparse(anchor.get("href", "")).query)
        values = query.get("c", [])
        if values and re.fullmatch(r"\d+_\d+", values[0]):
            category_id = values[0]
            category_name = anchor.get_text(" ", strip=True)
            if category_id == "3_1":
                break

    uploader_anchor = soup.select_one('a[href^="/user/"], a[href*="/user/"]')
    uploader = uploader_anchor.get_text(" ", strip=True) if uploader_anchor else None
    information_url = None
    info_label = soup.find(string=re.compile(r"^\s*Information\s*:?\s*$", re.I))
    if info_label and info_label.parent:
        row = info_label.parent.parent
        link = row.select_one("a[href]") if row else None
        if link:
            information_url = urljoin(base_url, link.get("href"))

    description_node = soup.select_one(".torrent-description, #torrent-description, .panel-body.markdown")
    description_html = None
    description_text = None
    if description_node:
        for unsafe in description_node.select("script, style, iframe, object, embed"):
            unsafe.decompose()
        raw = "".join(str(child) for child in description_node.contents)
        description_html = bleach.clean(
            raw,
            tags={"p", "br", "strong", "em", "b", "i", "ul", "ol", "li", "code", "pre", "blockquote", "a"},
            attributes={"a": ["href", "title"]},
            protocols={"http", "https"},
            strip=True,
        )
        description_text = BeautifulSoup(description_html, "lxml").get_text("\n", strip=True)

    files: list[TorrentFile] = []
    candidates = soup.select(".torrent-file-list li, #file-tree li, [data-file-size]")
    seen_paths: set[str] = set()
    for node in candidates:
        name_node = node.select_one(".file-name, a, span")
        path = (name_node.get_text(" ", strip=True) if name_node else node.get_text(" ", strip=True)).strip()
        size_text = node.get("data-file-size") or None
        if not size_text:
            size_node = node.select_one(".file-size, .text-muted")
            size_text = size_node.get_text(" ", strip=True) if size_node else None
        if size_text and path.endswith(size_text):
            path = path[: -len(size_text)].strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        files.append(TorrentFile(path=html.unescape(path), size=size_text, size_bytes=parse_size_bytes(size_text)))

    info_hash_match = INFO_HASH_RE.search(soup.get_text(" ", strip=True))
    info_hash = info_hash_match.group(1).upper() if info_hash_match else None
    published_text = _extract_labeled_value(soup, "Date")
    size = _extract_labeled_value(soup, "File size")
    published_at = None
    time_node = soup.select_one("time[datetime]")
    candidate_date = time_node.get("datetime") if time_node else published_text
    if candidate_date:
        try:
            published_at = date_parser.parse(candidate_date)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            published_at = published_at.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            published_at = None

    magnet_anchor = soup.select_one('a[href^="magnet:"]')
    torrent_anchor = soup.select_one('a[href$=".torrent"], a[href*="/download/"]')

    def labeled_int(*labels: str) -> int:
        for label in labels:
            value = _extract_labeled_value(soup, label)
            if value:
                match = re.search(r"\d[\d,]*", value)
                if match:
                    return int(match.group(0).replace(",", ""))
        return 0

    row_classes = " ".join(
        " ".join(node.get("class", [])) for node in soup.select(".panel, .row, tr")
    ).casefold()
    return {
        "title": title,
        "category_id": category_id,
        "category_name": category_name,
        "uploader": uploader,
        "information_url": information_url,
        "description_text": description_text,
        "description_html": description_html,
        "files": files,
        "info_hash": info_hash,
        "published_text": published_text,
        "published_at": published_at,
        "size": size,
        "seeders": labeled_int("Seeders"),
        "leechers": labeled_int("Leechers"),
        "downloads": labeled_int("Completed", "Downloads"),
        "comments": len(soup.select(".comment-panel, .comment")),
        "trusted": "trusted" in row_classes or bool(soup.find(string=re.compile(r"Trusted", re.I))),
        "remake": "remake" in row_classes or bool(soup.find(string=re.compile(r"Remake", re.I))),
        "magnet_url": magnet_anchor.get("href") if magnet_anchor else None,
        "torrent_url": urljoin(base_url, torrent_anchor.get("href")) if torrent_anchor else None,
    }


def find_torrent_id_in_html(html_text: str) -> int | None:
    soup = BeautifulSoup(html_text, "lxml")
    for anchor in soup.select('a[href*="/view/"]'):
        match = ID_RE.search(anchor.get("href", ""))
        if match:
            return int(match.group(1))
    return None
