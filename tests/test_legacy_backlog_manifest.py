from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_legacy_backlog_manifest_is_well_formed_and_still_references_indexed_dois() -> None:
    """Validate the immutable migration input, not today's mutable queue state.

    The 2026-08-12 legacy migration has already been materialized and audited.  Its
    manifest is a historical input, while current queue status, priority, evidence,
    completion state, and total backlog size are expected to evolve through later
    enrichment and editorial decisions.  Standing CI must therefore validate only
    properties that remain invariant after the one-shot migration.
    """

    manifest = _load("data/deep_analysis_migrations/legacy-backlog-triage-2026-08-12.json")
    doi_index = _load("data/doi_index.json")

    assert manifest["migration_id"] == "legacy-backlog-triage-2026-08-12-v1"
    assert manifest["cutoff_formal_batch_date"] == "2026-08-11"

    keep = manifest.get("keep")
    assert isinstance(keep, list)
    assert len(keep) == 43

    seen: set[str] = set()
    for decision in keep:
        assert isinstance(decision, dict)
        doi = str(decision.get("doi") or "").casefold()
        assert doi
        assert doi not in seen
        seen.add(doi)

        assert decision.get("priority_level") in {"P0", "P1", "P2", "P3"}
        assert decision.get("analysis_status", "pending") in {"pending", "deferred"}
        assert str(decision.get("category") or "").strip()
        assert str(decision.get("reason") or "").strip()

        # DOI history is durable even when a later editorial decision completes or
        # deselects an item and removes it from the active queue.
        assert doi in doi_index
