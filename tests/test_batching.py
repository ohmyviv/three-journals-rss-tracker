import json
import subprocess
import sys
from pathlib import Path


def test_batch_is_idempotent(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    for folder in ["data", "public/batches"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "doi_index.json").write_text(json.dumps({
        "10.1234/test": {
            "doi": "10.1234/test", "journal": "Nature", "current_title": "Test", "source_url": "https://example.org",
            "first_seen_at": "2026-07-27T09:00:00+08:00", "discovery_status": "live_discovery", "batch_id": None
        }
    }), encoding="utf-8")
    for name, value in {
        "pending_doi.json": {}, "deep_analysis_queue.json": {}, "batch_index.json": {}
    }.items():
        (tmp_path / "data" / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "public" / "latest_run.json").write_text(json.dumps({
        "run_id": "r1", "checked_at": "2026-07-27T10:30:00+08:00", "status": "success_new_items",
        "source_status": {"nature": "success", "science": "success", "cell": "success"}
    }), encoding="utf-8")
    command = [sys.executable, str(root / "scripts" / "build_daily_batch.py"), "--workspace", str(tmp_path), "--config", str(root / "config" / "tracker.yaml"), "--date", "2026-07-27"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    first = json.loads((tmp_path / "public" / "batches" / "2026-07-27.json").read_text(encoding="utf-8"))
    subprocess.run(command, check=True, capture_output=True, text=True)
    second = json.loads((tmp_path / "public" / "batches" / "2026-07-27.json").read_text(encoding="utf-8"))
    assert first == second
    assert first["counts"]["new_dois"] == 1


def test_batch_marks_crossref_fallback_as_degraded(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    for folder in ["data", "public/batches"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "doi_index.json").write_text("{}", encoding="utf-8")
    for name, value in {
        "pending_doi.json": {}, "deep_analysis_queue.json": {}, "batch_index.json": {},
        "source_state.json": {
            "nature": {"last_success_at": "2026-07-27T10:30:00+08:00"},
            "science": {"last_success_at": "2026-07-27T10:30:00+08:00"},
            "cell": {"last_success_at": "2026-07-27T10:30:00+08:00"},
        },
    }.items():
        (tmp_path / "data" / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "public" / "latest_run.json").write_text(json.dumps({
        "run_id": "r2", "checked_at": "2026-07-27T10:30:00+08:00", "status": "degraded_fallback_sources",
        "source_status": {"nature": "success", "science": "fallback_crossref", "cell": "fallback_crossref"},
    }), encoding="utf-8")
    command = [sys.executable, str(root / "scripts" / "build_daily_batch.py"), "--workspace", str(tmp_path), "--config", str(root / "config" / "tracker.yaml"), "--date", "2026-07-27"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    batch = json.loads((tmp_path / "public" / "batches" / "2026-07-27.json").read_text(encoding="utf-8"))
    assert batch["status"] == "degraded_sources"


def _write_batch_workspace(tmp_path: Path, *, doi_index: dict, latest_run: dict | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    for folder in ["data", "public/batches"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "doi_index.json").write_text(json.dumps(doi_index), encoding="utf-8")
    for name, value in {
        "pending_doi.json": {},
        "deep_analysis_queue.json": {},
        "batch_index.json": {},
        "source_state.json": {},
    }.items():
        (tmp_path / "data" / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "public" / "latest_run.json").write_text(json.dumps(latest_run or {
        "run_id": "r3",
        "checked_at": "2026-07-28T10:30:00+08:00",
        "status": "success_new_items",
        "source_status": {"nature": "success", "science": "success", "cell": "success"},
    }), encoding="utf-8")
    return root


def test_batch_refuses_early_freeze_without_force(tmp_path: Path):
    root = _write_batch_workspace(tmp_path, doi_index={})
    command = [
        sys.executable,
        str(root / "scripts" / "build_daily_batch.py"),
        "--workspace", str(tmp_path),
        "--config", str(root / "config" / "tracker.yaml"),
        "--date", "2026-07-28",
        "--now", "2026-07-28T00:14:00+08:00",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 3
    assert '"status": "too_early"' in result.stdout
    assert not (tmp_path / "public" / "batches" / "2026-07-28.json").exists()


def test_batch_rebuilds_premature_batch_after_cutoff(tmp_path: Path):
    batch_id = "daily-2026-07-28"
    root = _write_batch_workspace(tmp_path, doi_index={
        "10.1234/already": {
            "doi": "10.1234/already",
            "journal": "Nature",
            "current_title": "Already included",
            "source_url": "https://example.org/already",
            "first_seen_at": "2026-07-28T00:06:00+08:00",
            "discovery_status": "live_discovery",
            "batch_id": batch_id,
        },
        "10.1234/morning": {
            "doi": "10.1234/morning",
            "journal": "Science",
            "current_title": "Morning discovery",
            "source_url": "https://example.org/morning",
            "first_seen_at": "2026-07-28T11:30:00+08:00",
            "discovery_status": "live_discovery",
            "batch_id": None,
        },
    })
    premature = {
        "batch_id": batch_id,
        "date": "2026-07-28",
        "generated_at": "2026-07-28T00:14:00+08:00",
        "cutoff_at": "2026-07-28T12:17:00+08:00",
        "counts": {"new_dois": 1},
        "new_items": [{"doi": "10.1234/already"}],
    }
    path = tmp_path / "public" / "batches" / "2026-07-28.json"
    path.write_text(json.dumps(premature), encoding="utf-8")

    command = [
        sys.executable,
        str(root / "scripts" / "build_daily_batch.py"),
        "--workspace", str(tmp_path),
        "--config", str(root / "config" / "tracker.yaml"),
        "--date", "2026-07-28",
        "--now", "2026-07-28T12:18:00+08:00",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    batch = json.loads(path.read_text(encoding="utf-8"))
    assert batch["generated_at"] == "2026-07-28T12:18:00+08:00"
    assert batch["replaced_premature_batch"] is True
    assert batch["counts"]["new_dois"] == 2
    assert {item["doi"] for item in batch["new_items"]} == {"10.1234/already", "10.1234/morning"}
