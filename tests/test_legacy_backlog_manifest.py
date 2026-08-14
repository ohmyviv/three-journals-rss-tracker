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
    repository invariant. Likewise, the manifest's initial analysis_status is not a
    permanent invariant: later evidence enrichment can move queued records through
    pending_triage/awaiting_enrichment, and daily editorial decisions can complete or
    explicitly drop them.
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

    # The migration established a reviewed starting state, but later enrichment and
    # explicit daily dispositions are allowed to evolve it. Validate current durable
    # semantics instead of freezing the migration-day analysis_status forever.
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
        # Priority selected by the migration remains the default unless a later
        # explicit editorial decision changes it; current records without such a
        # change should still match the curated priority.
        assert queue[doi]["priority_level"] == decision["priority_level"]

    # Current explicit daily dispositions were outside the legacy migration scope.
    assert queue["10.1016/j.cell.2026.07.034"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.034"]["priority_level"] == "P0"
    assert queue["10.1016/j.cell.2026.07.029"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.024"]["analysis_status"] == "deferred"

    # Completed audit records remain terminal.
    assert queue["10.1016/j.cell.2026.07.027"]["analysis_status"] == "completed"

    # Legacy enrichment-only ghost state was normalized when explicitly curated.
    assert queue["10.1016/j.cell.2026.07.049"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.049"]["priority_level"] == "P0"

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
