from __future__ import annotations

import pytest

from three_journals_tracker.analysis_queue import apply_legacy_backlog_triage


TIMESTAMP = "2026-08-12T20:30:00+08:00"


def _queue_row(
    doi: str,
    title: str,
    *,
    status: str = "pending_triage",
    priority: str = "untriaged",
    last_reviewed_at: str | None = None,
    formal_date: str = "2026-08-07",
) -> dict:
    return {
        "doi": doi,
        "journal": "Nature",
        "title": title,
        "first_seen_at": f"{formal_date}T07:00:00+08:00",
        "queued_at": f"{formal_date}T07:00:00+08:00",
        "priority_level": priority,
        "priority_score": None,
        "queue_reason": [],
        "analysis_status": status,
        "target_complete_by": None,
        "defer_count": 0,
        "last_reviewed_at": last_reviewed_at,
        "first_formal_batch_id": f"daily-{formal_date}",
        "first_formal_batch_date": formal_date,
        "source_url": f"https://doi.org/{doi}",
    }


def _record(doi: str) -> dict:
    return {
        "doi": doi,
        "journal": "Nature",
        "current_title": doi,
    }


def test_legacy_triage_keeps_curated_and_removes_other_untouched_rows() -> None:
    keep_doi = "10.1000/keep"
    drop_doi = "10.1038/d41586-026-drop"
    current_doi = "10.1000/current"
    completed_doi = "10.1000/completed"

    queue = {
        keep_doi: _queue_row(keep_doi, "Virtual tissue foundation model"),
        drop_doi: _queue_row(drop_doi, "Daily briefing: unrelated item"),
        current_doi: _queue_row(
            current_doi,
            "Today's explicitly queued item",
            status="deferred",
            priority="P0",
            last_reviewed_at="2026-08-12T15:24:48+08:00",
            formal_date="2026-08-12",
        ),
        completed_doi: _queue_row(
            completed_doi,
            "Already completed",
            status="completed",
            priority="P1",
            last_reviewed_at="2026-08-11T17:44:51+08:00",
        ),
    }
    doi_index = {doi: _record(doi) for doi in queue}
    payload = {
        "migration_id": "legacy-test-v1",
        "cutoff_formal_batch_date": "2026-08-11",
        "keep": [
            {
                "doi": keep_doi,
                "priority_level": "P0",
                "analysis_status": "pending",
                "category": "ai_life_sciences",
                "reason": "High-value AI biology research.",
            }
        ],
    }

    history, counts = apply_legacy_backlog_triage(
        payload=payload,
        doi_index=doi_index,
        deep_queue=queue,
        timestamp=TIMESTAMP,
    )

    assert queue[keep_doi]["priority_level"] == "P0"
    assert queue[keep_doi]["analysis_status"] == "pending"
    assert queue[keep_doi]["last_reviewed_at"] == TIMESTAMP
    assert doi_index[keep_doi]["deep_analysis_disposition"] == "queued"

    assert drop_doi not in queue
    assert doi_index[drop_doi]["deep_analysis_disposition"] == "not_selected"

    assert queue[current_doi]["last_reviewed_at"] == "2026-08-12T15:24:48+08:00"
    assert queue[current_doi]["analysis_status"] == "deferred"
    assert queue[completed_doi]["analysis_status"] == "completed"

    assert counts["eligible_legacy_records"] == 2
    assert counts["kept_queued"] == 1
    assert counts["removed_not_selected"] == 1
    assert counts["active_backlog_before"] == 3
    assert counts["active_backlog_after"] == 2
    assert len(history) == 2
    assert {row["migration_id"] for row in history} == {"legacy-test-v1"}


def test_legacy_awaiting_enrichment_ghost_can_be_normalized_to_deferred() -> None:
    doi = "10.1000/apelin"
    queue = {
        doi: _queue_row(
            doi,
            "Structure-based design of receptor modulator",
            status="awaiting_enrichment",
        )
    }
    queue[doi]["queue_reason"] = ["metadata_only_awaiting_enrichment"]
    doi_index = {doi: _record(doi)}
    payload = {
        "migration_id": "legacy-test-v1",
        "cutoff_formal_batch_date": "2026-08-11",
        "keep": [
            {
                "doi": doi,
                "priority_level": "P0",
                "analysis_status": "deferred",
                "category": "structure_based_drug_design",
                "reason": "High-value target, waiting for evidence.",
                "queue_reason": ["awaiting_enrichment"],
            }
        ],
    }

    _, counts = apply_legacy_backlog_triage(
        payload=payload,
        doi_index=doi_index,
        deep_queue=queue,
        timestamp=TIMESTAMP,
    )

    assert queue[doi]["analysis_status"] == "deferred"
    assert queue[doi]["defer_count"] == 1
    assert queue[doi]["queue_reason"] == ["awaiting_enrichment"]
    assert counts["active_backlog_before"] == 0
    assert counts["active_backlog_after"] == 1


def test_legacy_triage_refuses_stale_keep_manifest() -> None:
    doi = "10.1000/already-reviewed"
    queue = {
        doi: _queue_row(
            doi,
            "Already reviewed",
            status="pending",
            priority="P1",
            last_reviewed_at="2026-08-10T12:00:00+08:00",
        )
    }
    doi_index = {doi: _record(doi)}
    payload = {
        "migration_id": "legacy-test-v1",
        "cutoff_formal_batch_date": "2026-08-11",
        "keep": [
            {
                "doi": doi,
                "priority_level": "P1",
                "analysis_status": "pending",
                "category": "test",
                "reason": "Should fail because the row is no longer untouched.",
            }
        ],
    }

    with pytest.raises(ValueError, match="no longer eligible"):
        apply_legacy_backlog_triage(
            payload=payload,
            doi_index=doi_index,
            deep_queue=queue,
            timestamp=TIMESTAMP,
        )
