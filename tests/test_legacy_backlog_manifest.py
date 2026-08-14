from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_real_legacy_backlog_manifest_matches_materialized_state() -> None:
    """The legacy migration is one-shot; later editorial/enrichment state may evolve.

    Re-applying the immutable manifest to the already-migrated queue is expected to
    fail its stale-manifest guard, so CI must not use re-application as a standing
    repository invariant. The manifest's migration-day analysis_status is likewise
    not permanent: enrichment can move a queued record through evidence states, and
    later daily editorial decisions can complete or explicitly drop it.
    """
    manifest = _load("data/deep_analysis_migrations/legacy-backlog-triage-2026-08-12.json")
    queue = _load("data/deep_analysis_queue.json")
    doi_index = _load("data/doi_index.json")

    keep = {item["doi"].casefold(): item for item in manifest["keep"]}
    active_statuses = {
        "pending_triage",
        "pending",
        "deferred",
        "awaiting_enrichment",
        "metadata_only_exhausted",
    }

    # The migration established a reviewed starting state. Standing CI validates
    # durable disposition semantics, not the mutable migration-day analysis_status.
    for doi, decision in keep.items():
        disposition = doi_index[doi].get("deep_analysis_disposition")
        assert disposition in {"queued", "completed", "not_selected"}

        if disposition == "not_selected":
            assert doi not in queue
            continue

        assert doi in queue
        assert queue[doi]["last_reviewed_at"]
        if disposition == "completed":
            assert queue[doi]["analysis_status"] == "completed"
            continue

        assert queue[doi]["analysis_status"] in active_statuses
        # Enrichment changes evidence/status, not the curated priority by itself.
        assert queue[doi]["priority_level"] == decision["priority_level"]

    # Explicit daily decisions outside the legacy migration may also evolve after
    # later evidence enrichment; verify durable queued semantics rather than a
    # specific transient analysis_status.
    for doi in (
        "10.1016/j.cell.2026.07.034",
        "10.1016/j.cell.2026.07.029",
        "10.1016/j.cell.2026.07.024",
    ):
        assert doi_index[doi]["deep_analysis_disposition"] == "queued"
        assert doi in queue
        assert queue[doi]["analysis_status"] in active_statuses
    assert queue["10.1016/j.cell.2026.07.034"]["priority_level"] == "P0"

    # Completed audit records remain terminal.
    assert doi_index["10.1016/j.cell.2026.07.027"]["deep_analysis_disposition"] == "completed"
    assert queue["10.1016/j.cell.2026.07.027"]["analysis_status"] == "completed"

    # Legacy enrichment-only ghost state stays durably triaged even if later
    # enrichment changes its transient analysis_status.
    ghost_doi = "10.1016/j.cell.2026.07.049"
    ghost_disposition = doi_index[ghost_doi]["deep_analysis_disposition"]
    assert ghost_disposition in {"queued", "completed", "not_selected"}
    if ghost_disposition == "not_selected":
        assert ghost_doi not in queue
    elif ghost_disposition == "completed":
        assert queue[ghost_doi]["analysis_status"] == "completed"
    else:
        assert queue[ghost_doi]["analysis_status"] in active_statuses
        assert queue[ghost_doi]["priority_level"] == "P0"

    # Obvious legacy contamination is durably not selected and absent from queue.
    assert "10.1038/s41586-026-10822-y" not in queue
    assert doi_index["10.1038/s41586-026-10822-y"]["deep_analysis_disposition"] == "not_selected"

    # The materialized queue is the compressed post-migration state, not the old
    # discovery-era backlog.
    active_backlog = sum(
        1 for row in queue.values() if row.get("analysis_status") in active_statuses
    )
    assert active_backlog <= 60
    assert len(queue) <= 80
