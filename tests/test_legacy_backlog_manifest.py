from __future__ import annotations

import copy
import json
from pathlib import Path

from three_journals_tracker.analysis_queue import apply_legacy_backlog_triage


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_real_legacy_backlog_manifest_is_applicable_and_compresses_queue() -> None:
    manifest = _load("data/deep_analysis_migrations/legacy-backlog-triage-2026-08-12.json")
    queue = copy.deepcopy(_load("data/deep_analysis_queue.json"))
    doi_index = copy.deepcopy(_load("data/doi_index.json"))

    history, counts = apply_legacy_backlog_triage(
        payload=manifest,
        doi_index=doi_index,
        deep_queue=queue,
        timestamp="2026-08-12T20:30:00+08:00",
    )

    assert counts["eligible_legacy_records"] >= 150
    assert counts["kept_queued"] == len(manifest["keep"])
    assert counts["removed_not_selected"] >= 100
    assert counts["active_backlog_after"] < counts["active_backlog_before"]
    assert counts["active_backlog_after"] <= 60
    assert len(history) == counts["eligible_legacy_records"]

    # Current explicit daily dispositions must remain untouched by the legacy migration.
    assert queue["10.1016/j.cell.2026.07.034"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.034"]["priority_level"] == "P0"
    assert queue["10.1016/j.cell.2026.07.029"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.024"]["analysis_status"] == "deferred"

    # Completed audit records remain completed.
    assert queue["10.1016/j.cell.2026.07.027"]["analysis_status"] == "completed"

    # Legacy enrichment-only ghost state becomes a real queued/deferred item when curated.
    assert queue["10.1016/j.cell.2026.07.049"]["analysis_status"] == "deferred"
    assert queue["10.1016/j.cell.2026.07.049"]["priority_level"] == "P0"

    # Obvious legacy contamination must leave active queue.
    assert "10.1038/s41586-026-10822-y" not in queue
    assert doi_index["10.1038/s41586-026-10822-y"]["deep_analysis_disposition"] == "not_selected"
