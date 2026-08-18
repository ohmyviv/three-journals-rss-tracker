from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dateutil import parser as date_parser

from .china_team import classify_china_team
from .feed_client import FeedEntry
from .normalize import clean_text


@dataclass(frozen=True)
class CrossrefResult:
    status: str
    http_status: int | None
    items: list[dict[str, Any]]
    error: str | None
    attempts: int
    duration_seconds: float
    pages: int
    query_mode: str
    query_start: str


def _crossref_date(parts: Any) -> str | None:
    try:
        values = parts.get("date-parts", [])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    if not values:
        return None
    year = int(values[0])
    month = int(values[1]) if len(values) > 1 else 1
    day = int(values[2]) if len(values) > 2 else 1
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat()


def _crossref_filter_date(value: str) -> str:
    parsed = date_parser.isoparse(value)
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    return parsed.astimezone(timezone.utc).date().isoformat()


def _best_item_date(item: dict[str, Any]) -> str | None:
    for field in ("published-online", "published-print", "published", "issued"):
        value = _crossref_date(item.get(field))
        if value:
            return value
    created = item.get("created")
    created_value = created.get("date-time") if isinstance(created, dict) else None
    return str(created_value) if created_value else None


def _crossref_affiliations(author: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for affiliation in author.get("affiliation") or []:
        if isinstance(affiliation, dict):
            name = clean_text(affiliation.get("name"))
        else:
            name = clean_text(affiliation)
        if name:
            values.append(name)
    return list(dict.fromkeys(values))


def crossref_item_to_entry(item: dict[str, Any]) -> FeedEntry:
    title_values = item.get("title") or []
    title = title_values[0] if isinstance(title_values, list) and title_values else str(title_values or "")
    doi = str(item.get("DOI") or "")
    url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
    authors: list[dict[str, str]] = []
    author_affiliations: list[dict[str, Any]] = []
    affiliations: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(part for part in [str(author.get("given") or "").strip(), str(author.get("family") or "").strip()] if part)
        row_affiliations = _crossref_affiliations(author)
        affiliations.extend(row_affiliations)
        if name:
            authors.append({"name": name})
        if name or row_affiliations:
            author_affiliations.append(
                {
                    "author": name,
                    "affiliations": row_affiliations,
                    "sequence": clean_text(author.get("sequence")) or None,
                    "corresponding": bool(author.get("corresponding")),
                }
            )
    affiliations = list(dict.fromkeys(affiliations))
    china_team = classify_china_team(
        author_affiliations=author_affiliations,
        affiliations=affiliations,
        source="crossref",
    )
    subjects = [{"term": str(value)} for value in item.get("subject") or [] if value]
    return FeedEntry(
        title=title,
        doi=doi,
        id=doi or url,
        link=url,
        links=[{"href": url, "rel": "alternate"}] if url else [],
        published=_best_item_date(item),
        authors=authors,
        author=", ".join(row["name"] for row in authors),
        author_affiliations=author_affiliations,
        affiliations=affiliations,
        tags=subjects,
        summary=str(item.get("abstract") or ""),
        crossref_type=str(item.get("type") or ""),
        container_title=(item.get("container-title") or [""])[0],
        **china_team,
    )


def calculate_query_window(
    *,
    checked_at: str,
    fallback_last_success_at: str | None,
    has_journal_history: bool,
    bootstrap_lookback_days: int,
    initial_live_lookback_days: int,
    overlap_hours: int,
) -> tuple[str, str, str]:
    checked = date_parser.isoparse(checked_at)
    if fallback_last_success_at:
        start = date_parser.isoparse(fallback_last_success_at) - timedelta(hours=overlap_hours)
        return "created", start.astimezone(timezone.utc).date().isoformat(), "live"
    if not has_journal_history:
        start = checked - timedelta(days=bootstrap_lookback_days)
        return "published", start.date().isoformat(), "bootstrap"
    start = checked - timedelta(days=initial_live_lookback_days)
    return "created", start.astimezone(timezone.utc).date().isoformat(), "live"


def fetch_crossref_works(
    *,
    issns: list[str],
    query_mode: str,
    query_start: str,
    user_agent: str,
    timeout_seconds: int,
    retries: int,
    backoff_seconds: list[int],
    rows: int = 1000,
    max_pages: int = 5,
    mailto: str | None = None,
) -> CrossrefResult:
    started = time.monotonic()
    all_items: dict[str, dict[str, Any]] = {}
    total_attempts = 0
    total_pages = 0
    last_status: int | None = None
    last_success_status: int | None = None
    successful_requests = 0
    errors: list[str] = []
    filter_name = "from-pub-date" if query_mode == "published" else "from-created-date"
    normalized_query_start = _crossref_filter_date(query_start)
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    for issn in list(dict.fromkeys(issns)):
        cursor = "*"
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "filter": f"type:journal-article,{filter_name}:{normalized_query_start}",
                "rows": rows,
                "cursor": cursor,
            }
            if mailto:
                params["mailto"] = mailto
            response_json: dict[str, Any] | None = None
            request_error: str | None = None
            for attempt in range(max(retries, 1)):
                total_attempts += 1
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)] if backoff_seconds else 0
                if wait:
                    time.sleep(wait)
                try:
                    response = requests.get(
                        f"https://api.crossref.org/journals/{issn}/works",
                        params=params,
                        headers=headers,
                        timeout=timeout_seconds,
                    )
                    last_status = response.status_code
                    if response.status_code == 200:
                        response_json = response.json()
                        last_success_status = response.status_code
                        successful_requests += 1
                        break
                    request_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if response.status_code < 500 and response.status_code != 429:
                        break
                except (requests.RequestException, ValueError) as exc:
                    request_error = f"{type(exc).__name__}: {exc}"
            if response_json is None:
                errors.append(f"ISSN {issn}: {request_error or 'Unknown Crossref error'}")
                break

            total_pages += 1
            message = response_json.get("message") or {}
            items = message.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                doi = str(item.get("DOI") or "").casefold().strip()
                if doi:
                    all_items[doi] = item
            next_cursor = message.get("next-cursor")
            if not next_cursor or len(items) < rows or next_cursor == cursor:
                break
            cursor = str(next_cursor)

    if successful_requests:
        status = "success"
        error = "; ".join(errors) if errors else None
    elif errors:
        status = "failed"
        error = "; ".join(errors)
    else:
        status = "success"
        error = None
    return CrossrefResult(
        status=status,
        http_status=last_success_status if successful_requests else last_status,
        items=list(all_items.values()),
        error=error,
        attempts=total_attempts,
        duration_seconds=round(time.monotonic() - started, 3),
        pages=total_pages,
        query_mode=query_mode,
        query_start=normalized_query_start,
    )
