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

from three_journals_tracker.analysis_queue import apply_legacy_backlog_triage
from three_journals_tracker.io_utils import append_jsonl, atomic_write_json, read_json
from three_journals_tracker.time_utils import now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply an auditable one-time triage to untouched legacy deep-analysis backlog rows"
    )
    parser.add_argument("migration_file", type=Path)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with args.config.open("r", encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    timestamp = now_iso(timezone_name)

    payload = json.loads(args.migration_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Legacy migration file must contain a JSON object")

    doi_index_path = args.workspace / "data" / "doi_index.json"
    queue_path = args.workspace / "data" / "deep_analysis_queue.json"
    history_path = args.workspace / "data" / "deep_analysis_history.jsonl"

    doi_index: dict[str, Any] = read_json(doi_index_path, {})
    queue: dict[str, Any] = read_json(queue_path, {})

    history, counts = apply_legacy_backlog_triage(
        payload=payload,
        doi_index=doi_index,
        deep_queue=queue,
        timestamp=timestamp,
    )

    output = {
        "migration_id": payload.get("migration_id"),
        "changed_at": timestamp,
        "dry_run": args.dry_run,
        "counts": counts,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    atomic_write_json(doi_index_path, doi_index)
    atomic_write_json(queue_path, queue)
    append_jsonl(history_path, history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
