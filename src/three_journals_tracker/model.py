from __future__ import annotations

from typing import Any

from .normalize import clean_text, extract_doi, temporary_item_key
from .time_utils import parse_any_datetime


def _entry_links(entry: Any) -> list[str]:
    links: list[str] = []
    for link in getattr(entry, "links", []) or []:
        href = getattr(link, "href", None) or (link.get("href") if isinstance(link, dict) else None)
        if href:
            links.append(str(href))
    direct = getattr(entry, "link", None)
    if direct:
        links.append(str(direct))
    return list(dict.fromkeys(links))


def _entry_doi_candidates(entry: Any) -> list[Any]:
    candidates: list[Any] = [
        getattr(entry, "doi", None),
        getattr(entry, "dc_identifier", None),
        getattr(entry, "prism_doi", None),
        getattr(entry, "id", None),
        getattr(entry, "guid", None),
        getattr(entry, "summary", None),
        getattr(entry, "description", None),
    ]
    candidates.extend(_entry_links(entry))
    for key, value in getattr(entry, "items", lambda: [])():
        lowered = str(key).casefold()
        if "doi" in lowered or "identifier" in lowered:
            candidates.append(value)
    return candidates


def entry_to_record(
    entry: Any,
    feed_id: str,
    journal: str,
    first_seen_at: str,
    timezone_name: str,
) -> dict[str, Any]:
    title = clean_text(getattr(entry, "title", None))
    links = _entry_links(entry)
    source_url = links[0] if links else clean_text(getattr(entry, "id", None))
    reported_time = None
    for field in ("published", "updated", "created", "date"):
        reported_time = parse_any_datetime(getattr(entry, field, None), timezone_name)
        if reported_time:
            break
    doi = extract_doi(*_entry_doi_candidates(entry))
    item_key = doi or temporary_item_key(journal, title, reported_time)
    authors: list[str] = []
    for author in getattr(entry, "authors", []) or []:
        name = getattr(author, "name", None) or (author.get("name") if isinstance(author, dict) else None)
        if name:
            authors.append(clean_text(name))
    if not authors and getattr(entry, "author", None):
        authors = [clean_text(getattr(entry, "author"))]
    tags: list[str] = []
    for tag in getattr(entry, "tags", []) or []:
        term = getattr(tag, "term", None) or (tag.get("term") if isinstance(tag, dict) else None)
        if term:
            tags.append(clean_text(term))
    return {
        "item_key": item_key,
        "doi": doi,
        "journal": journal,
        "feed_id": feed_id,
        "title": title,
        "source_url": source_url,
        "rss_reported_time": reported_time,
        "authors_rss": list(dict.fromkeys(authors)),
        "tags_rss": list(dict.fromkeys(tags)),
        "summary_rss": clean_text(getattr(entry, "summary", None) or getattr(entry, "description", None)),
        "first_seen_at": first_seen_at,
    }
