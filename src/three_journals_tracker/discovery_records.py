from __future__ import annotations

from typing import Any

from .model import entry_to_record
from .normalize import stable_event_id, temporary_item_key


SCHEDULER_RECORD_FIELDS = (
    "scheduled_for",
    "triggered_at",
    "scheduler_delay_minutes",
    "scheduler_delayed",
    "late_discovery_recovery",
    "intended_batch_date",
)


def journal_has_history(journal: str, doi_index: dict[str, Any], pending_doi: dict[str, Any]) -> bool:
    return any(row.get("journal") == journal for row in doi_index.values()) or any(
        row.get("journal") == journal for row in pending_doi.values()
    )


def _scheduler_fields(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {key: metadata.get(key) for key in SCHEDULER_RECORD_FIELDS if metadata.get(key) is not None}


def process_entries(
    *,
    entries: list[Any],
    feed_id: str,
    journal: str,
    checked_at: str,
    timezone_name: str,
    run_id: str,
    discovery_source: str,
    discovery_mode: str,
    previous_success_at: str | None,
    doi_index: dict[str, Any],
    pending_doi: dict[str, Any],
    deep_queue: dict[str, Any],
    run: dict[str, Any],
    events: list[dict[str, Any]],
    scheduler_metadata: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    source_new_count = 0
    source_new_doi_count = 0
    source_new_pending_count = 0
    default_status = "bootstrap_seed" if discovery_mode == "bootstrap" else "live_discovery"
    current_scheduler_fields = _scheduler_fields(scheduler_metadata)
    run.setdefault("counts", {}).setdefault("late_recovery_dois", 0)
    run.setdefault("counts", {}).setdefault("late_recovery_pending_doi", 0)

    for entry in entries:
        record = entry_to_record(entry, feed_id, journal, checked_at, timezone_name)
        record["metadata_source"] = discovery_source
        run["counts"]["items_seen"] += 1
        key, doi = record["item_key"], record["doi"]
        if doi and doi in doi_index:
            run["counts"]["duplicates"] += 1
            existing = doi_index[doi]
            existing["current_title"] = record["title"] or existing.get("current_title")
            existing["source_url"] = record["source_url"] or existing.get("source_url")
            existing["rss_reported_time"] = record["rss_reported_time"] or existing.get("rss_reported_time")
            existing["last_seen_at"] = checked_at
            existing["last_seen_source"] = discovery_source
            existing["last_updated_at"] = checked_at
            continue

        if doi:
            temporary_key = temporary_item_key(journal, record["title"], record["rss_reported_time"])
            prior_pending = pending_doi.pop(temporary_key, None)
            status = prior_pending.get("discovery_status", default_status) if prior_pending else default_status
            first_seen = prior_pending.get("first_seen_at") if prior_pending else checked_at
            last_absent = prior_pending.get("last_absent_at") if prior_pending else previous_success_at
            first_run = prior_pending.get("first_seen_run_id") if prior_pending else run_id
            scheduler_fields = (
                {key: prior_pending.get(key) for key in SCHEDULER_RECORD_FIELDS if prior_pending.get(key) is not None}
                if prior_pending
                else current_scheduler_fields
            )
            doi_index[doi] = {
                "doi": doi,
                "journal": journal,
                "title_first_seen": record["title"],
                "current_title": record["title"],
                "source_url": record["source_url"],
                "rss_reported_time": record["rss_reported_time"],
                "authors_rss": record["authors_rss"],
                "tags_rss": record["tags_rss"],
                "summary_rss": record["summary_rss"],
                "first_seen_at": first_seen,
                "last_absent_at": last_absent,
                "appearance_window_start": last_absent,
                "appearance_window_end": checked_at,
                "first_seen_run_id": first_run,
                "discovery_source": f"{feed_id}_{discovery_source}",
                "discovery_status": status,
                "enrichment_status": "crossref_only" if discovery_source == "crossref" else "pending",
                "batch_id": prior_pending.get("batch_id") if prior_pending else None,
                "batched_at": prior_pending.get("batched_at") if prior_pending else None,
                "last_seen_at": checked_at,
                "last_seen_source": discovery_source,
                "last_updated_at": checked_at,
                **scheduler_fields,
            }
            event = {
                "event_id": stable_event_id(feed_id, doi, checked_at),
                "run_id": run_id,
                **record,
                "discovery_status": status,
                "discovery_source": f"{feed_id}_{discovery_source}",
                "last_absent_at": last_absent,
                "appearance_window_start": last_absent,
                "appearance_window_end": checked_at,
                **scheduler_fields,
            }
            if prior_pending:
                event.update(event_type="doi_resolved", temporary_key=temporary_key)
                run["counts"]["resolved_dois"] += 1
            else:
                event["event_type"] = "first_discovery"
                target = "new_dois" if status == "live_discovery" else "seed_dois"
                run[target].append(doi)
                run["counts"][target] += 1
                if status == "live_discovery" and scheduler_fields.get("late_discovery_recovery"):
                    run["counts"]["late_recovery_dois"] += 1
                source_new_doi_count += 1
                source_new_count += 1
            events.append(event)
            if status == "live_discovery" and doi not in deep_queue:
                deep_queue[doi] = {
                    "doi": doi,
                    "journal": journal,
                    "title": record["title"],
                    "first_seen_at": first_seen,
                    "queued_at": checked_at,
                    "priority_level": "untriaged",
                    "priority_score": None,
                    "queue_reason": ["late_discovery_recovery"] if scheduler_fields.get("late_discovery_recovery") else [],
                    "analysis_status": "pending_triage",
                    "target_complete_by": None,
                    "defer_count": 0,
                    "last_reviewed_at": None,
                    **scheduler_fields,
                }
            continue

        if key in pending_doi:
            run["counts"]["duplicates"] += 1
            pending_doi[key]["last_seen_at"] = checked_at
            pending_doi[key]["last_updated_at"] = checked_at
            continue
        pending_doi[key] = {
            **record,
            "temporary_key": key,
            "discovery_status": default_status,
            "doi_status": "pending",
            "first_seen_run_id": run_id,
            "last_absent_at": previous_success_at,
            "appearance_window_start": previous_success_at,
            "appearance_window_end": checked_at,
            "last_seen_at": checked_at,
            "last_updated_at": checked_at,
            **current_scheduler_fields,
        }
        events.append({
            "event_id": stable_event_id(feed_id, key, checked_at),
            "run_id": run_id,
            **record,
            "discovery_status": default_status,
            "doi_status": "pending",
            "discovery_source": f"{feed_id}_{discovery_source}",
            "last_absent_at": previous_success_at,
            "appearance_window_start": previous_success_at,
            "appearance_window_end": checked_at,
            **current_scheduler_fields,
        })
        target = "new_pending_keys" if default_status == "live_discovery" else "seed_pending_keys"
        run[target].append(key)
        run["counts"]["new_pending_doi" if default_status == "live_discovery" else "seed_pending_doi"] += 1
        if default_status == "live_discovery" and current_scheduler_fields.get("late_discovery_recovery"):
            run["counts"]["late_recovery_pending_doi"] += 1
        source_new_pending_count += 1
        source_new_count += 1

    return source_new_count, source_new_doi_count, source_new_pending_count
