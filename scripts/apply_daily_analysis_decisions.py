#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from three_journals_tracker.analysis_queue import apply_daily_analysis_decisions
from three_journals_tracker.io_utils import append_jsonl, atomic_write_json, read_json
from three_journals_tracker.time_utils import now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply explicit daily deep-analysis dispositions for every formal new DOI "
            "and any historical backlog items reviewed in the same report"
        )
    )
    parser.add_argument("decision_file", type=Path)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    config = load_yaml(args.config)
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    timestamp = now_iso(timezone_name)

    payload = json.loads(args.decision_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Decision file must contain a JSON object")

    batch_id = str(payload.get("batch_id") or "")
    if not batch_id.startswith("daily-"):
        raise ValueError("batch_id must use daily-YYYY-MM-DD")
    batch_date = batch_id.removeprefix("daily-")
    batch_path = workspace / "public" / "batches" / f"{batch_date}.json"
    batch = read_json(batch_path, {})
    if not batch:
        raise FileNotFoundError(f"Formal batch not found: {batch_path}")

    doi_path = workspace / "data" / "doi_index.json"
    queue_path = workspace / "data" / "deep_analysis_queue.json"
    doi_index: dict[str, Any] = read_json(doi_path, {})
    deep_queue: dict[str, Any] = read_json(queue_path, {})

    history, counts = apply_daily_analysis_decisions(
        payload=payload,
        batch=batch,
        doi_index=doi_index,
        deep_queue=deep_queue,
        timestamp=timestamp,
    )

    atomic_write_json(doi_path, doi_index)
    atomic_write_json(queue_path, deep_queue)
    append_jsonl(workspace / "data" / "deep_analysis_history.jsonl", history)

    print(
        json.dumps(
            {
                "status": "applied",
                "batch_id": batch_id,
                "changed_at": timestamp,
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
