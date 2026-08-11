from __future__ import annotations

import pytest

from three_journals_tracker.analysis_queue import (
    active_backlog_count,
    apply_daily_analysis_decisions,
)


BATCH = {
    "batch_id": "daily-2026-08-11",
    "status": "success_new_items",
    "new_items": [
        {"doi": "10.1000/today-a"},
        {"doi": "10.1000/today-b"},
    ],
}


def _doi_index() -> dict:
    return {
        "10.1000/today-a": {
            "doi": "10.1000/today-a",
            "journal": "Nature",
            "current_title": "Today A",
            "first_seen_at": "2026-08-11T07:00:00+08:00",
            "batch_id": "daily-2026-08-11",
            "source_url": "https://example.org/a",
        },
        "10.1000/today-b": {
            "doi": "10.1000/today-b",
            "journal": "Cell",
            "current_title": "Today B",
            "first_seen_at": "2026-08-11T07:05:00+08:00",
            "batch_id": "daily-2026-08-11",
            "source_url": "https://example.org/b",
        },
        "10.1000/history": {
            "doi": "10.1000/history",
            "journal": "Science",
            "current_title": "Historical",
            "first_seen_at": "2026-08-01T07:00:00+08:00",
            "batch_id": "daily-2026-08-01",
            "source_url": "https://example.org/history",
        },
    }


def test_requires_explicit_decision_for_every_formal_new_doi() -> None:
    with pytest.raises(ValueError, match="missing: 10.1000/today-b"):
        apply_daily_analysis_decisions(
            payload={
                "batch_id": "daily-2026-08-11",
                "decisions": [
                    {
                        "doi": "10.1000/today-a",
                        "disposition": "not_selected",
                        "reason": "low relevance",
                    }
                ],
            },
            batch=BATCH,
            doi_index=_doi_index(),
            deep_queue={},
            timestamp="2026-08-11T15:30:00+08:00",
        )


def test_not_selected_doi_is_not_kept_in_queue() -> None:
    queue = {
        "10.1000/today-a": {
            "doi": "10.1000/today-a",
            "analysis_status": "pending_triage",
            "priority_level": "untriaged",
        }
    }
    index = _doi_index()
    history, counts = apply_daily_analysis_decisions(
        payload={
            "batch_id": "daily-2026-08-11",
            "decisions": [
                {
                    "doi": "10.1000/today-a",
                    "disposition": "not_selected",
                    "reason": "excluded article type",
                },
                {
                    "doi": "10.1000/today-b",
                    "disposition": "not_selected",
                    "reason": "outside investment scope",
                },
            ],
        },
        batch=BATCH,
        doi_index=index,
        deep_queue=queue,
        timestamp="2026-08-11T15:30:00+08:00",
    )

    assert queue == {}
    assert index["10.1000/today-a"]["deep_analysis_disposition"] == "not_selected"
    assert counts["not_selected"] == 2
    assert counts["active_backlog"] == 0
    assert len(history) == 2


def test_queued_doi_is_the_only_active_backlog_item() -> None:
    queue: dict = {}
    index = _doi_index()
    _, counts = apply_daily_analysis_decisions(
        payload={
            "batch_id": "daily-2026-08-11",
            "decisions": [
                {
                    "doi": "10.1000/today-a",
                    "disposition": "queued",
                    "reason": "high relevance but awaiting evidence enrichment",
                    "priority_level": "P1",
                    "analysis_status": "deferred",
                    "queue_reason": ["awaiting_enrichment"],
                },
                {
                    "doi": "10.1000/today-b",
                    "disposition": "not_selected",
                    "reason": "low relevance",
                },
            ],
        },
        batch=BATCH,
        doi_index=index,
        deep_queue=queue,
        timestamp="2026-08-11T15:30:00+08:00",
    )

    row = queue["10.1000/today-a"]
    assert row["analysis_status"] == "deferred"
    assert row["priority_level"] == "P1"
    assert row["first_formal_batch_id"] == "daily-2026-08-11"
    assert row["first_formal_batch_date"] == "2026-08-11"
    assert "10.1000/today-b" not in queue
    assert counts["active_backlog"] == 1


def test_completed_historical_item_is_logically_removed_from_backlog() -> None:
    queue = {
        "10.1000/history": {
            "doi": "10.1000/history",
            "journal": "Science",
            "title": "Historical",
            "analysis_status": "pending",
            "priority_level": "P1",
            "queued_at": "2026-08-01T07:00:00+08:00",
            "first_formal_batch_id": "daily-2026-08-01",
            "first_formal_batch_date": "2026-08-01",
        }
    }
    index = _doi_index()
    _, counts = apply_daily_analysis_decisions(
        payload={
            "batch_id": "daily-2026-08-11",
            "decisions": [
                {
                    "doi": "10.1000/today-a",
                    "disposition": "not_selected",
                    "reason": "low relevance",
                },
                {
                    "doi": "10.1000/today-b",
                    "disposition": "not_selected",
                    "reason": "low relevance",
                },
                {
                    "doi": "10.1000/history",
                    "disposition": "completed",
                    "reason": "deep analyzed in the 2026-08-11 report",
                },
            ],
        },
        batch=BATCH,
        doi_index=index,
        deep_queue=queue,
        timestamp="2026-08-11T15:30:00+08:00",
    )

    row = queue["10.1000/history"]
    assert row["analysis_status"] == "completed"
    assert row["completed_at"] == "2026-08-11T15:30:00+08:00"
    assert row["first_formal_batch_date"] == "2026-08-01"
    assert active_backlog_count(queue) == 0
    assert counts["completed"] == 1
    assert counts["historical_decisions"] == 1


def test_same_day_completed_item_is_recorded_but_not_active() -> None:
    queue: dict = {}
    index = _doi_index()
    _, counts = apply_daily_analysis_decisions(
        payload={
            "batch_id": "daily-2026-08-11",
            "decisions": [
                {
                    "doi": "10.1000/today-a",
                    "disposition": "completed",
                    "reason": "deep analyzed today",
                    "priority_level": "P0",
                },
                {
                    "doi": "10.1000/today-b",
                    "disposition": "not_selected",
                    "reason": "excluded article type",
                },
            ],
        },
        batch=BATCH,
        doi_index=index,
        deep_queue=queue,
        timestamp="2026-08-11T15:30:00+08:00",
    )

    assert queue["10.1000/today-a"]["analysis_status"] == "completed"
    assert active_backlog_count(queue) == 0
    assert index["10.1000/today-a"]["deep_analysis_disposition"] == "completed"
    assert counts["formal_new_decisions"] == 2


def test_completed_item_cannot_be_reclassified_as_not_selected() -> None:
    queue = {
        "10.1000/today-a": {
            "doi": "10.1000/today-a",
            "analysis_status": "completed",
            "priority_level": "P0",
        }
    }
    with pytest.raises(ValueError, match="cannot be reclassified"):
        apply_daily_analysis_decisions(
            payload={
                "batch_id": "daily-2026-08-11",
                "decisions": [
                    {
                        "doi": "10.1000/today-a",
                        "disposition": "not_selected",
                        "reason": "should fail",
                    },
                    {
                        "doi": "10.1000/today-b",
                        "disposition": "not_selected",
                        "reason": "low relevance",
                    },
                ],
            },
            batch=BATCH,
            doi_index=_doi_index(),
            deep_queue=queue,
            timestamp="2026-08-11T15:30:00+08:00",
        )
