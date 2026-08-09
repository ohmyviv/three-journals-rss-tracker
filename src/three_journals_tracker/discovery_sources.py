from __future__ import annotations

import os
from typing import Any

from .crossref_client import calculate_query_window, crossref_item_to_entry, fetch_crossref_works
from .feed_client import content_hash, fetch_feed, parse_feed


def collect_source(
    *,
    feed: dict[str, Any],
    config: dict[str, Any],
    checked_at: str,
    mode: str,
    previous: dict[str, Any],
    has_history: bool,
) -> dict[str, Any]:
    feed_id, journal = str(feed["id"]), str(feed["journal"])
    request = config.get("request", {})
    fallback = feed.get("fallback") or {}
    primary = fetch_feed(
        url=str(feed["url"]),
        user_agent=str(config.get("user_agent")),
        timeout_seconds=int(request.get("timeout_seconds", 30)),
        retries=1 if fallback else int(request.get("retries", 3)),
        backoff_seconds=list(request.get("backoff_seconds", [0, 30, 90])),
        etag=previous.get("etag"),
        last_modified=previous.get("last_modified"),
    )
    observation: dict[str, Any] = {
        "feed_id": feed_id,
        "journal": journal,
        "checked_at": checked_at,
        "previous_successful_check_at": previous.get("last_success_at"),
        "status": primary.status,
        "http_status": primary.http_status,
        "attempts": primary.attempts,
        "duration_seconds": primary.duration_seconds,
        "etag": primary.headers.get("etag") or previous.get("etag"),
        "last_modified": primary.headers.get("last-modified") or previous.get("last_modified"),
        "content_type": primary.headers.get("content-type"),
        "feed_hash": content_hash(primary.content) if primary.content else previous.get("feed_hash"),
        "feed_item_count": None,
        "new_item_count": 0,
        "new_doi_count": 0,
        "new_pending_count": 0,
        "error": primary.error,
        "discovery_source": "rss",
    }
    if primary.status == "not_modified":
        state = {
            **previous,
            "etag": observation["etag"],
            "last_modified": observation["last_modified"],
            "feed_hash": observation["feed_hash"],
            "last_success_at": checked_at,
            "last_checked_at": checked_at,
            "last_status": "not_modified",
            "last_error": None,
        }
        return {"success": True, "source_status": "not_modified", "entries": [], "observation": observation, "state": state, "source": "rss", "mode": mode}

    parse_error = None
    if primary.status == "success":
        meta, entries, parse_error = parse_feed(primary.content)
        observation.update(
            feed_item_count=len(entries),
            feed_title=meta.get("title"),
            feed_updated=meta.get("updated") or meta.get("published"),
        )
        if not (parse_error and not entries):
            state = {
                **previous,
                "etag": observation["etag"],
                "last_modified": observation["last_modified"],
                "feed_hash": observation["feed_hash"],
                "last_success_at": checked_at,
                "last_checked_at": checked_at,
                "last_status": "success",
                "last_error": parse_error,
            }
            return {"success": True, "source_status": "success", "entries": entries, "observation": observation, "state": state, "source": "rss", "mode": mode}

    if fallback.get("type") == "crossref":
        query_mode, query_start, fallback_mode = calculate_query_window(
            checked_at=checked_at,
            fallback_last_success_at=previous.get("fallback_last_success_at"),
            has_journal_history=has_history,
            bootstrap_lookback_days=int(fallback.get("bootstrap_lookback_days", 60)),
            initial_live_lookback_days=int(fallback.get("initial_live_lookback_days", 14)),
            overlap_hours=int(fallback.get("overlap_hours", 48)),
        )
        crossref = fetch_crossref_works(
            issns=[str(value) for value in fallback.get("issns", [])],
            query_mode=query_mode,
            query_start=query_start,
            user_agent=str(config.get("user_agent")),
            timeout_seconds=int(request.get("timeout_seconds", 30)),
            retries=int(request.get("retries", 3)),
            backoff_seconds=list(request.get("backoff_seconds", [0, 30, 90])),
            rows=int(fallback.get("rows", 1000)),
            max_pages=int(fallback.get("max_pages", 5)),
            mailto=config.get("crossref_mailto") or os.getenv("CROSSREF_MAILTO"),
        )
        observation.update(
            rss_status=primary.status if primary.status != "success" else "failed_parse",
            rss_http_status=primary.http_status,
            rss_error=primary.error or parse_error,
            fallback_type="crossref",
            fallback_status=crossref.status,
            fallback_http_status=crossref.http_status,
            fallback_attempts=crossref.attempts,
            fallback_duration_seconds=crossref.duration_seconds,
            fallback_pages=crossref.pages,
            fallback_query_mode=crossref.query_mode,
            fallback_query_start=crossref.query_start,
            fallback_error=crossref.error,
        )
        if crossref.status == "success":
            entries = [crossref_item_to_entry(item) for item in crossref.items]
            observation.update(
                status="fallback_success",
                http_status=crossref.http_status,
                attempts=primary.attempts + crossref.attempts,
                duration_seconds=round(primary.duration_seconds + crossref.duration_seconds, 3),
                content_type="application/json",
                feed_item_count=len(entries),
                error=primary.error or parse_error,
                discovery_source="crossref",
            )
            state = {
                **previous,
                "last_success_at": checked_at,
                "last_checked_at": checked_at,
                "last_status": "fallback_crossref",
                "last_error": primary.error or parse_error,
                "primary_last_status": primary.status if primary.status != "success" else "failed_parse",
                "primary_last_http_status": primary.http_status,
                "fallback_last_success_at": checked_at,
                "fallback_last_status": "success",
                "fallback_query_mode": crossref.query_mode,
                "fallback_query_start": crossref.query_start,
            }
            return {"success": True, "source_status": "fallback_crossref", "entries": entries, "observation": observation, "state": state, "source": "crossref", "mode": fallback_mode}

    status = "failed_parse" if parse_error else "failed"
    state = {
        **previous,
        "last_checked_at": checked_at,
        "last_status": status,
        "last_error": primary.error or parse_error or observation.get("fallback_error"),
    }
    return {"success": False, "source_status": status, "entries": [], "observation": observation, "state": state, "source": "rss", "mode": mode}
