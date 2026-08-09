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

from three_journals_tracker.io_utils import atomic_write_json, read_jsonl
from three_journals_tracker.stats import build_feed_statistics


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild feed update statistics")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    args = parser.parse_args()
    with args.config.open("r", encoding="utf-8") as handle:
        config: dict[str, Any] = yaml.safe_load(handle) or {}
    rows = read_jsonl(args.workspace / "data" / "feed_observations.jsonl")
    stats = build_feed_statistics(rows, str(config.get("timezone", "Asia/Shanghai")))
    atomic_write_json(args.workspace / "public" / "feed_statistics.json", stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
