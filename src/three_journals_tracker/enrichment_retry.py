from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as date_parser

from .china_team import classify_china_team
from .normalize import clean_text, normalize_doi


CHINA_HINT_FIELDS = {
    "author_affiliations",
    "affiliations",
    "china_team_status",
    "china_institutions",
    "china_key_authors",
    "china_team_evidence",
}


@dataclass(frozen=True)
class CrossrefWorkResult:
    status: str
    http_status: int | None
    item: dict[str, Any] | None
    error: str | None
    attempts: int
    duration_seconds: float


def merge_crossref_metadata(record: dict[str, Any], metadata: dict[str, Any]) -> None:
    """Merge enrichment metadata without letting an old `unknown` block a later team hint."""

    for key, value in metadata.items():
        if value in (None, "", []):
            continue
        if key == "china_team_status":
            if record.get(key) in (None, "", "unknown"):
                record[key] = value
            continue
        if key in CHINA_HINT_FIELDS:
            if not record.get(key):
                record[key] = value
            continue
        if key == "summary_rss" or not record.get(key):
            record[key] = value


def substantive_text(record: dict[str, Any], minimum_length: int = 40) -> str:
    """Return usable abstract-like text already stored on a DOI record."""

    for key in ("abstract", "summary_rss"):
        text = clean_text(record.get(key))
        if len(text) >= minimum_length:
            return text
    return ""


def formal_batch_fields(record: dict[str, Any]) -> dict[str, str]:
    """Derive immutable first-formal-batch fields from an assigned daily batch."""

    batch_id = str(record.get("first_formal_batch_id") or record.get("batch_id") or "")
    if not batch_id.startswith("daily-") or len(batch_id) != len("daily-YYYY-MM-DD"):
        return {}
    date_text = batch_id.removeprefix("daily-")
    try:
        datetime.fromisoformat(date_text)
    except ValueError:
        return {}
    return {
        "first_formal_batch_id": batch_id,
        "first_formal_batch_date": date_text,
    }


def queued_analysis_status_after_evidence(
    current_status: Any,
    *,
    was_evidence_waiting: bool,
) -> str:
    """Return canonical editorial status after substantive evidence becomes available.

    Evidence availability and editorial processing are separate dimensions.  Keep a
    deliberate pending/deferred decision unless the item was specifically deferred
    because evidence was missing.  Old enrichment-only statuses are normalized back
    into the current queued contract.
    """

    status = str(current_status or "")
    if status == "completed":
        return "completed"
    if status == "pending":
        return "pending"
    if status == "deferred":
        return "pending" if was_evidence_waiting else "deferred"
    if status in {"pending_triage", "awaiting_enrichment", "metadata_only_exhausted"}:
        return "pending"
    return "pending"


def queued_analysis_status_while_waiting(current_status: Any) -> str:
    """Keep evidence-poor queued work active using the canonical deferred status."""

    if str(current_status or "") == "completed":
        return "completed"
    return "deferred"


def completed_retry_days(record: dict[str, Any]) -> set[int]:
    values = record.get("crossref_enrichment_completed_days") or []
    completed: set[int] = set()
    for value in values:
        try:
            completed.add(int(value))
        except (TypeError, ValueError):
            continue
    return completed


def retry_day_due(
    record: dict[str, Any],
    *,
    now_at: datetime,
    retry_days: list[int],
    timezone_name: str,
) -> int | None:
    first_seen_text = record.get("first_seen_at")
    if not first_seen_text:
        return None
    tz = ZoneInfo(timezone_name)
    try:
        first_seen = date_parser.isoparse(str(first_seen_text)).astimezone(tz)
    except (TypeError, ValueError):
        return None
    age_days = (now_at.astimezone(tz).date() - first_seen.date()).days
    completed = completed_retry_days(record)
    for day in sorted({int(value) for value in retry_days if int(value) >= 0}):
        if age_days >= day and day not in completed:
            return day
    return None


def next_retry_at(
    record: dict[str, Any],
    *,
    retry_days: list[int],
    timezone_name: str,
) -> str | None:
    first_seen_text = record.get("first_seen_at")
    if not first_seen_text:
        return None
    tz = ZoneInfo(timezone_name)
    try:
        first_seen = date_parser.isoparse(str(first_seen_text)).astimezone(tz)
    except (TypeError, ValueError):
        return None
    completed = completed_retry_days(record)
    for day in sorted({int(value) for value in retry_days if int(value) >= 0}):
        if day not in completed:
            retry_date = first_seen.date() + timedelta(days=day)
            return datetime.combine(retry_date, datetime.min.time(), tzinfo=tz).isoformat()
    return None


def fetch_crossref_work(
    doi: str,
    *,
    user_agent: str,
    timeout_seconds: int,
    retries: int,
    backoff_seconds: list[int],
    mailto: str | None = None,
) -> CrossrefWorkResult:
    normalized = normalize_doi(doi)
    if not normalized:
        return CrossrefWorkResult("failed", None, None, "Invalid DOI", 0, 0.0)

    started = time.monotonic()
    attempts = 0
    last_status: int | None = None
    last_error: str | None = None
    params = {"mailto": mailto} if mailto else None
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    for attempt in range(max(retries, 1)):
        attempts += 1
        wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)] if backoff_seconds else 0
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(
                f"https://api.crossref.org/works/{quote(normalized, safe='/')}",
                params=params,
                headers=headers,
                timeout=timeout_seconds,
            )
            last_status = response.status_code
            if response.status_code == 200:
                payload = response.json()
                item = payload.get("message")
                if isinstance(item, dict):
                    return CrossrefWorkResult(
                        "success",
                        200,
                        item,
                        None,
                        attempts,
                        round(time.monotonic() - started, 3),
                    )
                last_error = "Crossref response did not contain a work object"
                break
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code < 500 and response.status_code != 429:
                break
        except (requests.RequestException, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    return CrossrefWorkResult(
        "failed",
        last_status,
        None,
        last_error or "Unknown Crossref error",
        attempts,
        round(time.monotonic() - started, 3),
    )


def _crossref_author_affiliations(author: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for affiliation in author.get("affiliation") or []:
        if isinstance(affiliation, dict):
            name = clean_text(affiliation.get("name"))
        else:
            name = clean_text(affiliation)
        if name:
            values.append(name)
    return list(dict.fromkeys(values))


def crossref_work_metadata(item: dict[str, Any]) -> dict[str, Any]:
    title_values = item.get("title") or []
    title = title_values[0] if isinstance(title_values, list) and title_values else clean_text(title_values)
    authors: list[str] = []
    author_affiliations: list[dict[str, Any]] = []
    affiliations: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = clean_text(" ".join(part for part in [author.get("given"), author.get("family")] if part))
        row_affiliations = _crossref_author_affiliations(author)
        affiliations.extend(row_affiliations)
        if name:
            authors.append(name)
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
    subjects = [clean_text(value) for value in item.get("subject") or [] if clean_text(value)]
    doi = normalize_doi(item.get("DOI"))
    return {
        "current_title": clean_text(title) or None,
        "source_url": clean_text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else None),
        "summary_rss": clean_text(item.get("abstract")) or None,
        "authors_rss": list(dict.fromkeys(authors)),
        "author_affiliations": author_affiliations,
        "affiliations": affiliations,
        "tags_rss": list(dict.fromkeys(subjects)),
        "crossref_type": clean_text(item.get("type")) or None,
        "container_title": clean_text((item.get("container-title") or [""])[0]) or None,
        **china_team,
    }
