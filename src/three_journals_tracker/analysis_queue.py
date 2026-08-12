from __future__ import annotations

from typing import Any

ACTIVE_ANALYSIS_STATUSES = {"pending_triage", "pending", "deferred"}
VALID_DECISIONS = {"completed", "queued", "not_selected"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "untriaged"}
VALID_QUEUED_STATUSES = {"pending", "deferred"}
LEGACY_TRIAGE_STATUSES = {
    "pending_triage",
    "pending",
    "awaiting_enrichment",
    "metadata_only_exhausted",
}


def active_backlog_count(queue: dict[str, Any]) -> int:
    return sum(
        1
        for row in queue.values()
        if row.get("analysis_status") in ACTIVE_ANALYSIS_STATUSES
    )


def _first_formal_batch_fields(record: dict[str, Any]) -> tuple[str | None, str | None]:
    batch_id = record.get("first_formal_batch_id") or record.get("batch_id")
    batch_date = record.get("first_formal_batch_date")
    if not batch_date and isinstance(batch_id, str) and batch_id.startswith("daily-"):
        batch_date = batch_id.removeprefix("daily-")
    return batch_id, batch_date


def _base_queue_row(
    doi: str,
    record: dict[str, Any],
    *,
    timestamp: str,
) -> dict[str, Any]:
    first_batch_id, first_batch_date = _first_formal_batch_fields(record)
    return {
        "doi": doi,
        "journal": record.get("journal"),
        "title": record.get("current_title") or record.get("title_first_seen"),
        "first_seen_at": record.get("first_seen_at"),
        "queued_at": timestamp,
        "priority_level": "untriaged",
        "priority_score": None,
        "queue_reason": [],
        "analysis_status": "pending",
        "target_complete_by": None,
        "defer_count": 0,
        "last_reviewed_at": timestamp,
        "first_formal_batch_id": first_batch_id,
        "first_formal_batch_date": first_batch_date,
        "source_url": record.get("source_url"),
    }


def apply_daily_analysis_decisions(
    *,
    payload: dict[str, Any],
    batch: dict[str, Any],
    doi_index: dict[str, Any],
    deep_queue: dict[str, Any],
    timestamp: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    batch_id = str(payload.get("batch_id") or "")
    if not batch_id:
        raise ValueError("Decision payload must include batch_id")
    if batch.get("batch_id") != batch_id:
        raise ValueError(
            f"Decision batch_id {batch_id!r} does not match formal batch {batch.get('batch_id')!r}"
        )
    if batch.get("status") == "blocked_failed_sources":
        raise ValueError("Cannot apply deep-analysis decisions to a blocked formal batch")

    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("Decision payload must include a decisions array")

    formal_new_dois = {
        str(item["doi"]).casefold()
        for item in batch.get("new_items", [])
        if item.get("doi")
    }

    decisions: dict[str, dict[str, Any]] = {}
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            raise ValueError("Each decision must be an object")
        doi = str(raw.get("doi") or "").casefold()
        if not doi:
            raise ValueError("Each decision must include doi")
        if doi in decisions:
            raise ValueError(f"Duplicate deep-analysis decision for DOI: {doi}")
        disposition = str(raw.get("disposition") or "")
        if disposition not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid disposition for {doi}: {disposition!r}; "
                f"expected one of {sorted(VALID_DECISIONS)}"
            )
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Decision for {doi} must include a non-empty reason")
        decisions[doi] = {**raw, "doi": doi, "disposition": disposition, "reason": reason}

    missing = sorted(formal_new_dois - set(decisions))
    if missing:
        raise ValueError(
            "Every DOI in the formal daily batch needs an explicit deep-analysis "
            f"decision; missing: {', '.join(missing)}"
        )

    history: list[dict[str, Any]] = []
    counts = {"completed": 0, "queued": 0, "not_selected": 0}

    for doi, decision in decisions.items():
        record = doi_index.get(doi)
        existing_queue = deep_queue.get(doi)
        if record is None and existing_queue is None:
            raise KeyError(f"DOI not present in doi_index or deep-analysis queue: {doi}")

        before_queue = dict(existing_queue) if existing_queue else None
        before_disposition = record.get("deep_analysis_disposition") if record else None
        disposition = decision["disposition"]
        priority = str(
            decision.get("priority_level")
            or (existing_queue or {}).get("priority_level")
            or "untriaged"
        )
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority_level for {doi}: {priority}")

        if disposition == "not_selected":
            if existing_queue and existing_queue.get("analysis_status") == "completed":
                raise ValueError(f"Completed DOI cannot be reclassified as not_selected: {doi}")
            deep_queue.pop(doi, None)
            after_queue = None
        else:
            if existing_queue and existing_queue.get("analysis_status") == "completed" and disposition == "queued":
                raise ValueError(f"Completed DOI cannot be re-queued: {doi}")
            row = dict(existing_queue) if existing_queue else _base_queue_row(
                doi,
                record or {},
                timestamp=timestamp,
            )
            row["priority_level"] = priority
            row["priority_score"] = decision.get(
                "priority_score",
                row.get("priority_score"),
            )
            row["target_complete_by"] = decision.get(
                "target_complete_by",
                row.get("target_complete_by"),
            )
            row["last_reviewed_at"] = timestamp
            if decision.get("queue_reason") is not None:
                queue_reason = decision["queue_reason"]
                if not isinstance(queue_reason, list):
                    raise ValueError(f"queue_reason for {doi} must be an array")
                row["queue_reason"] = queue_reason

            if disposition == "completed":
                row["analysis_status"] = "completed"
                row["completed_at"] = timestamp
                row["completed_in_batch_id"] = batch_id
            else:
                queued_status = str(decision.get("analysis_status") or "pending")
                if queued_status not in VALID_QUEUED_STATUSES:
                    raise ValueError(
                        f"Queued DOI {doi} must use analysis_status pending or deferred"
                    )
                row["analysis_status"] = queued_status
                if queued_status == "deferred" and (
                    not existing_queue
                    or existing_queue.get("analysis_status") != "deferred"
                ):
                    row["defer_count"] = int(row.get("defer_count", 0)) + 1
                row.pop("completed_at", None)
                row.pop("completed_in_batch_id", None)

            deep_queue[doi] = row
            after_queue = dict(row)

        if record is not None:
            record["deep_analysis_disposition"] = disposition
            record["deep_analysis_decided_at"] = timestamp
            record["deep_analysis_decision_batch_id"] = batch_id
            record["deep_analysis_reason"] = decision["reason"]
            record["deep_analysis_priority_level"] = priority
            if disposition == "completed":
                record["deep_analysis_completed_at"] = timestamp
                record["deep_analysis_completed_batch_id"] = batch_id
            doi_index[doi] = record

        history.append(
            {
                "changed_at": timestamp,
                "decision_batch_id": batch_id,
                "doi": doi,
                "disposition": disposition,
                "reason": decision["reason"],
                "before_disposition": before_disposition,
                "before_queue": before_queue,
                "after_queue": after_queue,
            }
        )
        counts[disposition] += 1

    counts["active_backlog"] = active_backlog_count(deep_queue)
    counts["tracked_queue_records"] = len(deep_queue)
    counts["formal_new_dois"] = len(formal_new_dois)
    counts["formal_new_decisions"] = len(formal_new_dois & set(decisions))
    counts["historical_decisions"] = len(set(decisions) - formal_new_dois)
    return history, counts


def _legacy_drop_reason(row: dict[str, Any]) -> tuple[str, str]:
    title = str(row.get("title") or "")
    lowered = title.casefold()
    excluded_prefixes = (
        "author correction:",
        "publisher correction:",
        "editorial expression of concern:",
        "editor's note:",
        "daily briefing:",
        "briefing chat:",
    )
    if lowered.startswith(excluded_prefixes):
        return (
            "excluded_article_type",
            "Legacy backlog triage: excluded correction/editorial/briefing-type item; it should not occupy deep-analysis capacity.",
        )

    out_of_scope_terms = (
        "quantum",
        "superconductor",
        "perovskite",
        "photovolta",
        "solar eclipse",
        "telescope",
        "methane emissions",
        "wildfire",
        "triassic",
        "venus",
        "climate",
        "rift valleys",
        "merchant ships",
        "battery",
        "thermopower",
        "fermi",
    )
    if any(term in lowered for term in out_of_scope_terms):
        return (
            "out_of_scope",
            "Legacy backlog triage: topic is outside the life-science/biomedical/AI-biology investment scope and is removed from active deep analysis.",
        )

    doi = str(row.get("doi") or "").casefold()
    if doi.startswith("10.1038/d41586-"):
        return (
            "secondary_or_commentary",
            "Legacy backlog triage: Nature news/feature/commentary item was reviewed but is not retained as a primary deep-analysis object; primary research is preferred.",
        )

    return (
        "lower_priority_or_unclear",
        "Legacy backlog triage: reviewed at the available title/metadata level and did not meet the current relevance, article-type, evidence-value, or investment-priority threshold for future deep analysis.",
    )


def apply_legacy_backlog_triage(
    *,
    payload: dict[str, Any],
    doi_index: dict[str, Any],
    deep_queue: dict[str, Any],
    timestamp: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply an auditable, one-time triage to untouched legacy queue rows.

    Eligibility is deliberately narrow: an item must still be untriaged, must never
    have been manually reviewed, must use a legacy queue status, and must belong to
    a formal batch on or before the manifest cutoff.  Anything already completed,
    explicitly queued by the current daily workflow, or manually reviewed is left
    untouched.
    """

    migration_id = str(payload.get("migration_id") or "").strip()
    cutoff_date = str(payload.get("cutoff_formal_batch_date") or "").strip()
    raw_keep = payload.get("keep")
    if not migration_id:
        raise ValueError("Legacy triage payload must include migration_id")
    if not cutoff_date:
        raise ValueError("Legacy triage payload must include cutoff_formal_batch_date")
    if not isinstance(raw_keep, list):
        raise ValueError("Legacy triage payload must include a keep array")

    keep: dict[str, dict[str, Any]] = {}
    for raw in raw_keep:
        if not isinstance(raw, dict):
            raise ValueError("Each legacy keep decision must be an object")
        doi = str(raw.get("doi") or "").casefold()
        if not doi:
            raise ValueError("Each legacy keep decision must include doi")
        if doi in keep:
            raise ValueError(f"Duplicate legacy keep DOI: {doi}")
        priority = str(raw.get("priority_level") or "")
        if priority not in {"P0", "P1", "P2", "P3"}:
            raise ValueError(f"Legacy keep DOI {doi} needs explicit P0-P3 priority")
        status = str(raw.get("analysis_status") or "pending")
        if status not in VALID_QUEUED_STATUSES:
            raise ValueError(f"Legacy keep DOI {doi} must use pending or deferred")
        reason = str(raw.get("reason") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not reason or not category:
            raise ValueError(f"Legacy keep DOI {doi} needs reason and category")
        keep[doi] = {**raw, "doi": doi, "priority_level": priority, "analysis_status": status}

    active_before = active_backlog_count(deep_queue)
    tracked_before = len(deep_queue)
    eligible: dict[str, dict[str, Any]] = {}
    for doi, row in deep_queue.items():
        formal_date = str(row.get("first_formal_batch_date") or "")
        if (
            row.get("priority_level") == "untriaged"
            and not row.get("last_reviewed_at")
            and row.get("analysis_status") in LEGACY_TRIAGE_STATUSES
            and formal_date
            and formal_date <= cutoff_date
        ):
            eligible[doi.casefold()] = row

    missing_keep = sorted(set(keep) - set(eligible))
    if missing_keep:
        raise ValueError(
            "Curated legacy keep list contains DOI(s) that are no longer eligible; "
            "refuse stale migration: " + ", ".join(missing_keep)
        )

    history: list[dict[str, Any]] = []
    kept = 0
    removed = 0
    priority_counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    category_counts: dict[str, int] = {}

    for doi in sorted(eligible):
        before_queue = dict(deep_queue[doi])
        record = doi_index.get(doi)
        if record is None:
            raise KeyError(f"Legacy queue DOI missing from doi_index: {doi}")
        before_disposition = record.get("deep_analysis_disposition")

        if doi in keep:
            decision = keep[doi]
            row = dict(deep_queue[doi])
            status = decision["analysis_status"]
            row["priority_level"] = decision["priority_level"]
            row["analysis_status"] = status
            row["last_reviewed_at"] = timestamp
            if decision.get("queue_reason") is not None:
                queue_reason = decision["queue_reason"]
                if not isinstance(queue_reason, list):
                    raise ValueError(f"queue_reason for {doi} must be an array")
                row["queue_reason"] = queue_reason
            if status == "deferred" and before_queue.get("analysis_status") != "deferred":
                row["defer_count"] = int(row.get("defer_count", 0)) + 1
            deep_queue[doi] = row
            disposition = "queued"
            reason = str(decision["reason"])
            category = str(decision["category"])
            after_queue: dict[str, Any] | None = dict(row)
            kept += 1
            priority_counts[decision["priority_level"]] += 1
        else:
            category, reason = _legacy_drop_reason(before_queue)
            deep_queue.pop(doi, None)
            disposition = "not_selected"
            after_queue = None
            removed += 1

        category_counts[category] = category_counts.get(category, 0) + 1
        record["deep_analysis_disposition"] = disposition
        record["deep_analysis_decided_at"] = timestamp
        record["deep_analysis_reason"] = reason
        record["deep_analysis_priority_level"] = (
            keep[doi]["priority_level"] if doi in keep else "untriaged"
        )
        record["deep_analysis_migration_id"] = migration_id
        doi_index[doi] = record

        history.append(
            {
                "changed_at": timestamp,
                "migration_id": migration_id,
                "migration_type": "legacy_backlog_triage",
                "doi": doi,
                "disposition": disposition,
                "category": category,
                "reason": reason,
                "before_disposition": before_disposition,
                "before_queue": before_queue,
                "after_queue": after_queue,
            }
        )

    counts = {
        "eligible_legacy_records": len(eligible),
        "kept_queued": kept,
        "removed_not_selected": removed,
        "preserved_records": tracked_before - len(eligible),
        "active_backlog_before": active_before,
        "active_backlog_after": active_backlog_count(deep_queue),
        "tracked_queue_records_before": tracked_before,
        "tracked_queue_records_after": len(deep_queue),
        **{f"kept_{priority}": count for priority, count in priority_counts.items()},
    }
    for category, count in sorted(category_counts.items()):
        counts[f"category_{category}"] = count
    return history, counts
