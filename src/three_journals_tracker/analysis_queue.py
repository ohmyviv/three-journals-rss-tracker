from __future__ import annotations

from typing import Any

ACTIVE_ANALYSIS_STATUSES = {"pending_triage", "pending", "deferred"}
VALID_DECISIONS = {"completed", "queued", "not_selected"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "untriaged"}
VALID_QUEUED_STATUSES = {"pending", "deferred"}


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
