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

from three_journals_tracker.io_utils import append_jsonl, atomic_write_json, read_json
from three_journals_tracker.time_utils import now_iso

VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "untriaged"}
VALID_STATUSES = {"pending_triage", "pending", "deferred", "completed", "standard_only", "dropped"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply deterministic deep-analysis queue decisions from JSON")
    parser.add_argument("decision_file", type=Path)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with args.config.open("r", encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    timestamp = now_iso(timezone_name)
    decisions = json.loads(args.decision_file.read_text(encoding="utf-8"))
    if not isinstance(decisions, list):
        raise ValueError("Decision file must contain a JSON array")
    queue_path = args.workspace / "data" / "deep_analysis_queue.json"
    queue: dict[str, Any] = read_json(queue_path, {})
    history: list[dict[str, Any]] = []
    for decision in decisions:
        doi = str(decision["doi"]).casefold()
        if doi not in queue:
            raise KeyError(f"DOI not present in queue: {doi}")
        priority = decision.get("priority_level", queue[doi].get("priority_level", "untriaged"))
        status = decision.get("analysis_status", queue[doi].get("analysis_status", "pending_triage"))
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority_level: {priority}")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid analysis_status: {status}")
        before = dict(queue[doi])
        queue[doi].update({
            "priority_level": priority,
            "priority_score": decision.get("priority_score", queue[doi].get("priority_score")),
            "queue_reason": decision.get("queue_reason", queue[doi].get("queue_reason", [])),
            "analysis_status": status,
            "target_complete_by": decision.get("target_complete_by", queue[doi].get("target_complete_by")),
            "last_reviewed_at": timestamp,
        })
        if status == "deferred":
            queue[doi]["defer_count"] = int(queue[doi].get("defer_count", 0)) + 1
        if status == "completed":
            queue[doi]["completed_at"] = timestamp
        history.append({"changed_at": timestamp, "doi": doi, "before": before, "after": queue[doi]})
    atomic_write_json(queue_path, queue)
    append_jsonl(args.workspace / "data" / "deep_analysis_history.jsonl", history)
    print(json.dumps({"updated": len(history), "changed_at": timestamp}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
