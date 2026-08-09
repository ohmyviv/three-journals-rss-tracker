from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from typing import Any

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:)]}>\"'"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value: Any) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(value: Any) -> str | None:
    if value is None:
        return None
    text = urllib.parse.unquote(clean_text(value)).strip()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", text, flags=re.IGNORECASE)
    match = DOI_PATTERN.search(text)
    if not match:
        return None
    doi = match.group(0).rstrip(TRAILING_PUNCTUATION).casefold()
    return doi or None


def extract_doi(*values: Any) -> str | None:
    for value in values:
        doi = normalize_doi(value)
        if doi:
            return doi
    return None


def temporary_item_key(journal: str, title: str, reported_date: str | None) -> str:
    canonical = "|".join([journal.casefold(), normalize_title(title), reported_date or ""])
    return "tmp:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def stable_event_id(feed_id: str, item_key: str, first_seen_at: str) -> str:
    canonical = "|".join([feed_id, item_key, first_seen_at])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
