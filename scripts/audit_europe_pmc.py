#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from three_journals_tracker.discovery_records import process_entries
from three_journals_tracker.europe_pmc_client import (
    europe_pmc_item_to_entry,
    europe_pmc_metadata,
    fetch_europe_pmc,
)
from three_journals_tracker.io_utils import append_jsonl, atomic_write_json, read_json
from three_journals_tracker.normalize import normalize_doi
from three_journals_tracker.scheduler import build_scheduler_event, write_scheduler_event
from three_journals_tracker.time_utils import now_iso


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Science and Cell coverage with Europe PMC")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=["auto", "bootstrap", "live"], default="auto")
    parser.add_argument("--feeds", type=Path, default=ROOT / "config" / "feeds.yaml")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    parser.add_argument("--now", help="Override current timestamp (ISO 8601; mainly for tests)")
    parser.add_argument("--trigger-type", default=os.getenv("GITHUB_EVENT_NAME", "manual"))
    parser.add_argument("--schedule-expression", default=os.getenv("SCHEDULE_EXPRESSION", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    feeds_config = load_yaml(args.feeds)
    config = load_yaml(args.config)
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    if args.now:
        checked_at = date_parser.isoparse(args.now).astimezone(ZoneInfo(timezone_name)).replace(microsecond=0).isoformat()
    else:
        checked_at = now_iso(timezone_name)
    checked_dt = date_parser.isoparse(checked_at)
    audit_config = config.get("europe_pmc", {})
    request_config = config.get("request", {})
    run_id = "europe-pmc-" + checked_at.replace(":", "").replace("+", "p")
    scheduler_event = build_scheduler_event(
        workflow="europe_pmc_audit",
        triggered_at=checked_at,
        trigger_type=str(args.trigger_type or "manual"),
        schedule_expression=str(args.schedule_expression or "") or None,
        timezone_name=timezone_name,
        delay_threshold_minutes=int(config.get("scheduler", {}).get("delay_threshold_minutes", 15)),
    )

    doi_index: dict[str, Any] = read_json(workspace / "data" / "doi_index.json", {})
    pending_doi: dict[str, Any] = read_json(workspace / "data" / "pending_doi.json", {})
    deep_queue: dict[str, Any] = read_json(workspace / "data" / "deep_analysis_queue.json", {})
    audit_state: dict[str, Any] = read_json(workspace / "data" / "europe_pmc_state.json", {})
    events: list[dict[str, Any]] = []

    run: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "checked_at": checked_at,
        "trigger_type": scheduler_event["trigger_type"],
        "schedule_expression_utc": scheduler_event["schedule_expression_utc"],
        "scheduled_for": scheduler_event["scheduled_for"],
        "triggered_at": scheduler_event["triggered_at"],
        "scheduler_delay_minutes": scheduler_event["delay_minutes"],
        "scheduler_delayed": scheduler_event["scheduler_delayed"],
        "status": "running",
        "counts": {
            "items_seen": 0,
            "new_dois": 0,
            "seed_dois": 0,
            "new_pending_doi": 0,
            "seed_pending_doi": 0,
            "resolved_dois": 0,
            "duplicates": 0,
            "records_enriched": 0,
        },
        "new_dois": [],
        "seed_dois": [],
        "new_pending_keys": [],
        "seed_pending_keys": [],
        "journals": {},
    }

    successful = 0
    enabled = 0
    lookback_days = int(audit_config.get("lookback_days", 30))
    for feed in feeds_config.get("feeds", []):
        europe_config = feed.get("europe_pmc") or {}
        if not europe_config.get("enabled", False):
            continue
        enabled += 1
        feed_id = str(feed["id"])
        journal = str(feed["journal"])
        previous = audit_state.get(feed_id, {})
        audit_mode = args.mode
        if audit_mode == "auto":
            audit_mode = "live" if previous.get("last_success_at") else "bootstrap"
        start_date = checked_dt.date() - timedelta(days=lookback_days)
        result = fetch_europe_pmc(
            journal_name=str(europe_config.get("journal_query") or journal),
            start_date=start_date,
            end_date=checked_dt.date(),
            expected_issns=[str(value) for value in europe_config.get("issns", [])],
            timeout_seconds=int(request_config.get("timeout_seconds", 30)),
            retries=int(request_config.get("retries", 3)),
            backoff_seconds=list(request_config.get("backoff_seconds", [0, 30, 90])),
            page_size=int(audit_config.get("page_size", 1000)),
            max_pages=int(audit_config.get("max_pages", 5)),
            email=config.get("europe_pmc_email") or os.getenv("EUROPE_PMC_EMAIL"),
        )
        journal_report = {
            "mode": audit_mode,
            "query": result.query,
            "status": result.status,
            "http_status": result.http_status,
            "attempts": result.attempts,
            "duration_seconds": result.duration_seconds,
            "pages": result.pages,
            "items_returned": len(result.items),
            "error": result.error,
        }
        run["journals"][feed_id] = journal_report
        if result.status != "success":
            audit_state[feed_id] = {
                **previous,
                "last_checked_at": checked_at,
                "last_status": "failed",
                "last_error": result.error,
            }
            continue

        successful += 1
        entries = [europe_pmc_item_to_entry(item) for item in result.items]
        before_counts = dict(run["counts"])
        process_entries(
            entries=entries,
            feed_id=feed_id,
            journal=journal,
            checked_at=checked_at,
            timezone_name=timezone_name,
            run_id=run_id,
            discovery_source="europe_pmc",
            discovery_mode=audit_mode,
            previous_success_at=previous.get("last_success_at"),
            doi_index=doi_index,
            pending_doi=pending_doi,
            deep_queue=deep_queue,
            run=run,
            events=events,
        )

        enriched = 0
        for item in result.items:
            doi = normalize_doi(item.get("doi"))
            if not doi or doi not in doi_index:
                continue
            metadata = europe_pmc_metadata(item)
            record = doi_index[doi]
            changed = False
            for key, value in metadata.items():
                if value not in (None, "", []) and record.get(key) != value:
                    record[key] = value
                    changed = True
            record["europe_pmc_last_seen_at"] = checked_at
            if not record.get("europe_pmc_first_seen_at"):
                record["europe_pmc_first_seen_at"] = checked_at
            if changed:
                record["enrichment_status"] = "europe_pmc_enriched"
                record["last_updated_at"] = checked_at
                enriched += 1
        run["counts"]["records_enriched"] += enriched
        journal_report.update(
            new_dois=run["counts"]["new_dois"] - before_counts["new_dois"],
            seed_dois=run["counts"]["seed_dois"] - before_counts["seed_dois"],
            duplicates=run["counts"]["duplicates"] - before_counts["duplicates"],
            records_enriched=enriched,
        )
        audit_state[feed_id] = {
            "last_checked_at": checked_at,
            "last_success_at": checked_at,
            "last_status": "success",
            "last_error": result.error,
            "last_query": result.query,
            "last_mode": audit_mode,
        }

    if enabled == 0:
        run["status"] = "disabled"
    elif successful == enabled:
        run["status"] = "success"
    elif successful:
        run["status"] = "partial_success"
    else:
        run["status"] = "failed"
    run["completed_at"] = now_iso(timezone_name)

    append_jsonl(workspace / "data" / "discovery_events.jsonl", events)
    atomic_write_json(workspace / "data" / "doi_index.json", doi_index)
    atomic_write_json(workspace / "data" / "pending_doi.json", pending_doi)
    atomic_write_json(workspace / "data" / "deep_analysis_queue.json", deep_queue)
    atomic_write_json(workspace / "data" / "europe_pmc_state.json", audit_state)
    atomic_write_json(workspace / "public" / "europe_pmc_audit.json", run)
    run_path = workspace / "runs" / "audits" / checked_at[:4] / checked_at[5:7] / f"{run_id}.json"
    atomic_write_json(run_path, run)
    write_scheduler_event(
        workspace,
        {
            **scheduler_event,
            "run_id": run_id,
            "outcome": run["status"],
            "completed_at": run["completed_at"],
        },
    )
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 1 if run["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
