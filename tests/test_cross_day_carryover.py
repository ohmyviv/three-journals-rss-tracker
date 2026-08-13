from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from three_journals_tracker.batching import cross_day_carryover_info


def test_cross_day_carryover_info_uses_local_first_seen_date() -> None:
    carryover, from_date = cross_day_carryover_info(
        {"first_seen_at": "2026-08-13T17:41:29+08:00"},
        target_date="2026-08-14",
        timezone_name="Asia/Shanghai",
    )
    assert carryover is True
    assert from_date == "2026-08-13"

    same_day, same_day_from = cross_day_carryover_info(
        {"first_seen_at": "2026-08-14T07:12:00+08:00"},
        target_date="2026-08-14",
        timezone_name="Asia/Shanghai",
    )
    assert same_day is False
    assert same_day_from is None

    late_recovery, late_recovery_from = cross_day_carryover_info(
        {
            "first_seen_at": "2026-08-14T12:25:00+08:00",
            "late_discovery_recovery": True,
            "intended_batch_date": "2026-08-14",
        },
        target_date="2026-08-14",
        timezone_name="Asia/Shanghai",
    )
    assert late_recovery is False
    assert late_recovery_from is None


def test_next_day_batch_marks_unbatched_prior_day_discoveries_as_carryover(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    for folder in ["data", "public/batches"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)

    doi_index = {
        "10.1000/carryover": {
            "doi": "10.1000/carryover",
            "journal": "Nature",
            "current_title": "Discovered after yesterday's freeze",
            "source_url": "https://example.org/carryover",
            "first_seen_at": "2026-08-13T17:41:29+08:00",
            "discovery_status": "live_discovery",
            "batch_id": None,
        },
        "10.1000/same-day": {
            "doi": "10.1000/same-day",
            "journal": "Cell",
            "current_title": "Normal same-day discovery",
            "source_url": "https://example.org/same-day",
            "first_seen_at": "2026-08-14T07:12:00+08:00",
            "discovery_status": "live_discovery",
            "batch_id": None,
        },
    }
    pending_doi = {
        "pending-carryover": {
            "temporary_key": "pending-carryover",
            "journal": "Science",
            "title": "Prior-day item without DOI",
            "source_url": "https://example.org/pending-carryover",
            "first_seen_at": "2026-08-13T20:30:00+08:00",
            "discovery_status": "live_discovery",
            "batch_id": None,
        }
    }

    (tmp_path / "data" / "doi_index.json").write_text(json.dumps(doi_index), encoding="utf-8")
    (tmp_path / "data" / "pending_doi.json").write_text(json.dumps(pending_doi), encoding="utf-8")
    for name, value in {
        "deep_analysis_queue.json": {},
        "batch_index.json": {},
        "source_state.json": {
            "nature": {"last_success_at": "2026-08-14T08:00:00+08:00"},
            "science": {"last_success_at": "2026-08-14T08:00:00+08:00"},
            "cell": {"last_success_at": "2026-08-14T08:00:00+08:00"},
        },
    }.items():
        (tmp_path / "data" / name).write_text(json.dumps(value), encoding="utf-8")

    (tmp_path / "public" / "latest_run.json").write_text(
        json.dumps({
            "run_id": "discover-2026-08-14T080000p0800",
            "checked_at": "2026-08-14T08:00:00+08:00",
            "status": "success_new_items",
            "source_status": {"nature": "success", "science": "success", "cell": "success"},
        }),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(root / "scripts" / "build_daily_batch.py"),
        "--workspace", str(tmp_path),
        "--config", str(root / "config" / "tracker.yaml"),
        "--date", "2026-08-14",
        "--now", "2026-08-14T11:00:00+08:00",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)

    batch = json.loads(
        (tmp_path / "public" / "batches" / "2026-08-14.json").read_text(encoding="utf-8")
    )
    items = {item["doi"]: item for item in batch["new_items"]}
    assert items["10.1000/carryover"]["cross_day_carryover"] is True
    assert items["10.1000/carryover"]["carryover_from_date"] == "2026-08-13"
    assert items["10.1000/same-day"]["cross_day_carryover"] is False
    assert items["10.1000/same-day"]["carryover_from_date"] is None

    pending = batch["missing_doi_items"][0]
    assert pending["cross_day_carryover"] is True
    assert pending["carryover_from_date"] == "2026-08-13"

    assert batch["counts"]["cross_day_carryover_dois"] == 1
    assert batch["counts"]["cross_day_carryover_missing_doi"] == 1
    assert "cross_day_carryover" in batch["flags"]

    batch_index = json.loads((tmp_path / "data" / "batch_index.json").read_text(encoding="utf-8"))
    indexed = batch_index["daily-2026-08-14"]
    assert indexed["cross_day_carryover_doi_count"] == 1
    assert indexed["cross_day_carryover_missing_doi_count"] == 1
