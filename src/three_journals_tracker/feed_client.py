from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class FetchResult:
    status: str
    http_status: int | None
    content: bytes
    headers: dict[str, str]
    error: str | None
    attempts: int
    duration_seconds: float


class FeedEntry(dict[str, Any]):
    """Dictionary with feedparser-like attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def fetch_feed(
    url: str,
    user_agent: str,
    timeout_seconds: int,
    retries: int,
    backoff_seconds: list[int],
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    started = time.monotonic()
    last_error: str | None = None
    last_status: int | None = None
    attempts = 0
    for attempt in range(max(retries, 1)):
        attempts = attempt + 1
        wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)] if backoff_seconds else 0
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
            last_status = response.status_code
            selected_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"etag", "last-modified", "content-type", "date"}
            }
            if response.status_code == 304:
                return FetchResult("not_modified", 304, b"", selected_headers, None, attempts, round(time.monotonic() - started, 3))
            if response.status_code == 200 and response.content:
                return FetchResult("success", 200, response.content, selected_headers, None, attempts, round(time.monotonic() - started, 3))
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    return FetchResult("failed", last_status, b"", {}, last_error or "Unknown fetch error", attempts, round(time.monotonic() - started, 3))


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    if ":" in tag:
        prefix, local = tag.split(":", 1)
        return prefix, local
    return "", tag


def _text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _field_name(namespace: str, local: str) -> str:
    lower_ns = namespace.casefold()
    lower_local = local.casefold()
    if "prism" in lower_ns:
        return f"prism_{lower_local}"
    if "purl.org/dc" in lower_ns or lower_ns.endswith("dc"):
        return f"dc_{lower_local}"
    return lower_local.replace("-", "_")


def _parse_entry(element: ET.Element) -> FeedEntry:
    entry = FeedEntry()
    links: list[dict[str, str]] = []
    authors: list[dict[str, str]] = []
    tags: list[dict[str, str]] = []
    for child in list(element):
        namespace, local = _split_tag(child.tag)
        field = _field_name(namespace, local)
        lower_local = local.casefold()
        if lower_local == "link":
            href = child.attrib.get("href") or _text(child)
            if href:
                links.append({"href": href, "rel": child.attrib.get("rel", "alternate")})
                entry.setdefault("link", href)
            continue
        if lower_local == "author":
            name = ""
            for grandchild in list(child):
                _, grand_local = _split_tag(grandchild.tag)
                if grand_local.casefold() == "name":
                    name = _text(grandchild)
                    break
            name = name or _text(child)
            if name:
                authors.append({"name": name})
            continue
        if lower_local == "category":
            term = child.attrib.get("term") or _text(child)
            if term:
                tags.append({"term": term})
            continue
        value = _text(child)
        if not value:
            continue
        if field not in entry:
            entry[field] = value
        else:
            existing = entry[field]
            entry[field] = existing + " " + value if isinstance(existing, str) else value
        if lower_local == "guid":
            entry.setdefault("id", value)
        if field == "dc_date":
            entry.setdefault("date", value)
        if field == "dc_identifier":
            entry.setdefault("identifier", value)
    if links:
        entry["links"] = links
    if authors:
        entry["authors"] = authors
        entry["author"] = ", ".join(author["name"] for author in authors)
    if tags:
        entry["tags"] = tags
    return entry


def parse_feed(content: bytes) -> tuple[dict[str, Any], list[FeedEntry], str | None]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        return {}, [], f"XML ParseError: {exc}"

    entries: list[FeedEntry] = []
    for element in root.iter():
        _, local = _split_tag(element.tag)
        if local.casefold() in {"item", "entry"}:
            entries.append(_parse_entry(element))

    feed_meta: dict[str, Any] = {}
    channel = None
    for element in root.iter():
        _, local = _split_tag(element.tag)
        if local.casefold() in {"channel", "feed"}:
            channel = element
            break
    if channel is not None:
        for child in list(channel):
            _, local = _split_tag(child.tag)
            if local.casefold() in {"item", "entry"}:
                continue
            value = _text(child)
            if value:
                feed_meta.setdefault(local.casefold().replace("-", "_"), value)
    return feed_meta, entries, None


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
