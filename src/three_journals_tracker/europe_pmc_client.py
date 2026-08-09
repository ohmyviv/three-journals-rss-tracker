from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from .feed_client import FeedEntry
from .normalize import clean_text, normalize_doi


@dataclass(frozen=True)
class EuropePmcResult:
    status: str
    http_status: int | None
    items: list[dict[str, Any]]
    error: str | None
    attempts: int
    duration_seconds: float
    pages: int
    query: str


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _author_name(author: dict[str, Any]) -> str:
    full_name = clean_text(author.get("fullName"))
    if full_name:
        return full_name
    return clean_text(" ".join(part for part in [author.get("firstName"), author.get("lastName")] if part))


def _journal_issns(item: dict[str, Any]) -> set[str]:
    journal = ((item.get("journalInfo") or {}).get("journal") or {})
    return {
        clean_text(value)
        for value in [journal.get("issn"), journal.get("essn")]
        if clean_text(value)
    }


def item_matches_journal(item: dict[str, Any], journal_name: str, expected_issns: list[str]) -> bool:
    expected = {clean_text(value) for value in expected_issns if clean_text(value)}
    actual = _journal_issns(item)
    if expected and actual:
        return bool(expected & actual)
    journal = ((item.get("journalInfo") or {}).get("journal") or {})
    title = clean_text(journal.get("title") or item.get("journalTitle"))
    return title.casefold() == journal_name.casefold()


def europe_pmc_item_to_entry(item: dict[str, Any]) -> FeedEntry:
    doi = normalize_doi(item.get("doi")) or ""
    source = clean_text(item.get("source"))
    external_id = clean_text(item.get("id") or item.get("pmid"))
    article_url = f"https://europepmc.org/article/{source}/{external_id}" if source and external_id else ""
    source_url = f"https://doi.org/{doi}" if doi else article_url

    authors: list[dict[str, str]] = []
    author_rows = ((item.get("authorList") or {}).get("author") or [])
    for author in _as_list(author_rows):
        if not isinstance(author, dict):
            continue
        name = _author_name(author)
        if name:
            authors.append({"name": name})

    publication_types = ((item.get("pubTypeList") or {}).get("pubType") or [])
    tags = [{"term": clean_text(value)} for value in _as_list(publication_types) if clean_text(value)]
    journal = ((item.get("journalInfo") or {}).get("journal") or {})

    return FeedEntry(
        title=clean_text(item.get("title")),
        doi=doi,
        id=doi or article_url or external_id,
        link=source_url,
        links=[{"href": source_url, "rel": "alternate"}] if source_url else [],
        published=clean_text(item.get("firstPublicationDate") or item.get("electronicPublicationDate")),
        authors=authors,
        author=", ".join(row["name"] for row in authors),
        tags=tags,
        summary=clean_text(item.get("abstractText")),
        europe_pmc_source=source,
        europe_pmc_id=external_id,
        pmid=clean_text(item.get("pmid")),
        pmcid=clean_text(item.get("pmcid")),
        journal_title=clean_text(journal.get("title") or item.get("journalTitle")),
        issn=clean_text(journal.get("issn")),
        essn=clean_text(journal.get("essn")),
    )


def europe_pmc_metadata(item: dict[str, Any]) -> dict[str, Any]:
    author_rows = ((item.get("authorList") or {}).get("author") or [])
    authors: list[str] = []
    affiliations: list[str] = []
    for author in _as_list(author_rows):
        if not isinstance(author, dict):
            continue
        name = _author_name(author)
        if name:
            authors.append(name)
        details = ((author.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or [])
        for detail in _as_list(details):
            if isinstance(detail, dict):
                affiliation = clean_text(detail.get("affiliation"))
                if affiliation:
                    affiliations.append(affiliation)

    publication_types = ((item.get("pubTypeList") or {}).get("pubType") or [])
    journal = ((item.get("journalInfo") or {}).get("journal") or {})
    return {
        "pmid": clean_text(item.get("pmid")) or None,
        "pmcid": clean_text(item.get("pmcid")) or None,
        "europe_pmc_source": clean_text(item.get("source")) or None,
        "europe_pmc_id": clean_text(item.get("id")) or None,
        "abstract": clean_text(item.get("abstractText")) or None,
        "authors": list(dict.fromkeys(authors)),
        "affiliations": list(dict.fromkeys(affiliations)),
        "publication_types": list(dict.fromkeys(clean_text(value) for value in _as_list(publication_types) if clean_text(value))),
        "journal_title": clean_text(journal.get("title") or item.get("journalTitle")) or None,
        "issn": clean_text(journal.get("issn")) or None,
        "essn": clean_text(journal.get("essn")) or None,
        "first_publication_date": clean_text(item.get("firstPublicationDate")) or None,
        "electronic_publication_date": clean_text(item.get("electronicPublicationDate")) or None,
        "first_index_date": clean_text(item.get("firstIndexDate")) or None,
    }


def fetch_europe_pmc(
    *,
    journal_name: str,
    start_date: date,
    end_date: date,
    expected_issns: list[str],
    timeout_seconds: int,
    retries: int,
    backoff_seconds: list[int],
    page_size: int = 1000,
    max_pages: int = 5,
    email: str | None = None,
) -> EuropePmcResult:
    query = f'JOURNAL:"{journal_name}" AND FIRST_PDATE:[{start_date.isoformat()} TO {end_date.isoformat()}]'
    cursor = "*"
    pages = 0
    attempts = 0
    last_status: int | None = None
    errors: list[str] = []
    deduped: dict[str, dict[str, Any]] = {}
    started = time.monotonic()

    for _ in range(max_pages):
        payload: dict[str, Any] | None = None
        request_error: str | None = None
        params: dict[str, Any] = {
            "query": query,
            "resultType": "core",
            "format": "json",
            "pageSize": page_size,
            "cursorMark": cursor,
            "sort": "P_PDATE_D desc",
        }
        if email:
            params["email"] = email
        for attempt in range(max(retries, 1)):
            attempts += 1
            wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)] if backoff_seconds else 0
            if wait:
                time.sleep(wait)
            try:
                response = requests.get(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "three-journals-rss-tracker/0.3"},
                    timeout=timeout_seconds,
                )
                last_status = response.status_code
                if response.status_code == 200:
                    payload = response.json()
                    break
                request_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code < 500 and response.status_code != 429:
                    break
            except (requests.RequestException, ValueError) as exc:
                request_error = f"{type(exc).__name__}: {exc}"
        if payload is None:
            errors.append(request_error or "Unknown Europe PMC error")
            break

        pages += 1
        result_rows = ((payload.get("resultList") or {}).get("result") or [])
        for item in _as_list(result_rows):
            if not isinstance(item, dict) or not item_matches_journal(item, journal_name, expected_issns):
                continue
            doi = normalize_doi(item.get("doi"))
            key = doi or f"{clean_text(item.get('source'))}:{clean_text(item.get('id'))}"
            if key and key != ":":
                deduped[key] = item
        next_cursor = payload.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor or len(_as_list(result_rows)) < page_size:
            break
        cursor = str(next_cursor)

    status = "failed" if errors and not deduped else "success"
    return EuropePmcResult(
        status=status,
        http_status=last_status,
        items=list(deduped.values()),
        error="; ".join(errors) if errors else None,
        attempts=attempts,
        duration_seconds=round(time.monotonic() - started, 3),
        pages=pages,
        query=query,
    )
