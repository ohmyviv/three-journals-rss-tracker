#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from three_journals_tracker.enrichment_retry import (
    completed_retry_days,
    crossref_work_metadata,
    fetch_crossref_work,
    formal_batch_fields,
    next_retry_at,
    retry_day_due,
    substantive_text,
)
from three_journals_tracker.io_utils import atomic_write_json, read_json
from three_journals_tracker.normalize import clean_text
from three_journals_tracker.time_utils import now_iso


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry delayed Crossref abstract enrichment")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    parser.add_argument("--now", help="Override current timestamp (ISO 8601; mainly for tests)")
    return parser.parse_args()


def append_reason(queue_record: dict[str, Any], reason: str) -> None:
    reasons = [str(value) for value in queue_record.get("queue_reason") or []]
    if reason not in reasons:
        reasons.append(reason)
    queue_record["queue_reason"] = reasons


def sync_formal_batch_fields(record: dict[str, Any], queue_record: dict[str, Any] | None) -> bool:
    fields = formal_batch_fields(record)
    if not fields:
        return False
    changed = False
    for key, value in fields.items():
        if not record.get(key):
            record[key] = value
            changed = True
        if queue_record is not None and not queue_record.get(key):
            queue_record[key] = value
            changed = True
    return changed


def promote_evidence(
    record: dict[str, Any],
    queue_record: dict[str, Any] | None,
    *,
    checked_at: str,
    reason: str,
) -> None:
    abstract = clean_text(record.get("abstract"))
    summary = clean_text(record.get("summary_rss"))
    if abstract and not summary:
        record["summary_rss"] = abstract
        summary = abstract
    if not summary:
        return

    record["evidence_level"] = "abstract_available"
    record["last_enrichment_success_at"] = checked_at
    if abstract:
        record["enrichment_status"] = "europe_pmc_enriched"
    elif record.get("enrichment_status") in {None, "pending", "crossref_only", "metadata_only_exhausted"}:
        record["enrichment_status"] = "crossref_enriched"

    if queue_record is not None:
        queue_record["evidence_level"] = "abstract_available"
        queue_record["last_enrichment_success_at"] = checked_at
        if queue_record.get("analysis_status") in {
            "awaiting_enrichment",
            "metadata_only_exhausted",
            "pending",
            "deferred",
        }:
            queue_record["analysis_status"] = "pending_triage"
        append_reason(queue_record, reason)


def mark_waiting(
    record: dict[str, Any],
    queue_record: dict[str, Any] | None,
    *,
    retry_days: list[int],
    timezone_name: str,
) -> None:
    record["evidence_level"] = "metadata_only"
    record["next_enrichment_retry_at"] = next_retry_at(
        record,
        retry_days=retry_days,
        timezone_name=timezone_name,
    )
    if queue_record is not None:
        queue_record["evidence_level"] = "metadata_only"
        if queue_record.get("analysis_status") in {
            None,
            "pending_triage",
            "pending",
            "deferred",
            "awaiting_enrichment",
        }:
            queue_record["analysis_status"] = "awaiting_enrichment"
        queue_record["next_enrichment_retry_at"] = record.get("next_enrichment_retry_at")
        append_reason(queue_record, "metadata_only_awaiting_enrichment")


def mark_exhausted(record: dict[str, Any], queue_record: dict[str, Any] | None, checked_at: str) -> None:
    record["evidence_level"] = "metadata_only"
    record["enrichment_status"] = "metadata_only_exhausted"
    record["next_enrichment_retry_at"] = None
    record["enrichment_exhausted_at"] = checked_at
    if queue_record is not None:
        queue_record["evidence_level"] = "metadata_only"
        if queue_record.get("analysis_status") in {
            None,
            "pending_triage",
            "pending",
            "deferred",
            "awaiting_enrichment",
        }:
            queue_record["analysis_status"] = "metadata_only_exhausted"
        queue_record["next_enrichment_retry_at"] = None
        queue_record["enrichment_exhausted_at"] = checked_at
        append_reason(queue_record, "metadata_only_exhausted")


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    config = load_yaml(args.config)
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    tz = ZoneInfo(timezone_name)
    now_at = date_parser.isoparse(args.now).astimezone(tz) if args.now else datetime.now(tz)
    now_at = now_at.replace(microsecond=0)
    checked_at = now_at.isoformat()

    retry_config = config.get("crossref_enrichment", {})
    retry_days = sorted({int(value) for value in retry_config.get("retry_days", [3, 7, 14])})
    max_records = int(retry_config.get("max_records_per_run", 100))
    request_config = config.get("request", {})

    doi_path = workspace / "data" / "doi_index.json"
    queue_path = workspace / "data" / "deep_analysis_queue.json"
    doi_index: dict[str, Any] = read_json(doi_path, {})
    deep_queue: dict[str, Any] = read_json(queue_path, {})

    run: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": checked_at,
        "timezone": timezone_name,
        "retry_days": retry_days,
        "status": "running",
        "counts": {
            "doi_records_seen": len(doi_index),
            "formal_batch_fields_backfilled": 0,
            "crossref_metadata_only": 0,
            "awaiting_enrichment": 0,
            "due": 0,
            "attempted": 0,
            "enriched_from_existing_europe_pmc": 0,
            "enriched_from_crossref": 0,
            "still_metadata_only": 0,
            "exhausted": 0,
            "failed": 0,
        },
        "attempted_dois": [],
        "enriched_dois": [],
        "exhausted_dois": [],
        "failures": [],
    }

    due_records: list[tuple[str, dict[str, Any], int]] = []
    for doi, record in doi_index.items():
        queue_record = deep_queue.get(doi)
        if sync_formal_batch_fields(record, queue_record):
            run["counts"]["formal_batch_fields_backfilled"] += 1
        if queue_record is not None and not queue_record.get("source_url") and record.get("source_url"):
            queue_record["source_url"] = record.get("source_url")

        existing_text = substantive_text(record)
        if existing_text:
            was_waiting = record.get("evidence_level") == "metadata_only"
            source_reason = "europe_pmc_abstract_enriched" if clean_text(record.get("abstract")) else "abstract_available"
            promote_evidence(record, queue_record, checked_at=checked_at, reason=source_reason)
            if was_waiting and clean_text(record.get("abstract")):
                run["counts"]["enriched_from_existing_europe_pmc"] += 1
            continue

        discovery_source = str(record.get("discovery_source") or "")
        if record.get("discovery_status") != "live_discovery" or "crossref" not in discovery_source:
            continue

        run["counts"]["crossref_metadata_only"] += 1
        mark_waiting(record, queue_record, retry_days=retry_days, timezone_name=timezone_name)
        run["counts"]["awaiting_enrichment"] += 1

        completed = completed_retry_days(record)
        if retry_days and all(day in completed for day in retry_days):
            mark_exhausted(record, queue_record, checked_at)
            run["counts"]["exhausted"] += 1
            run["exhausted_dois"].append(doi)
            continue

        due_day = retry_day_due(
            record,
            now_at=now_at,
            retry_days=retry_days,
            timezone_name=timezone_name,
        )
        if due_day is not None and len(due_records) < max_records:
            due_records.append((doi, record, due_day))

    run["counts"]["due"] = len(due_records)
    for doi, record, due_day in due_records:
        queue_record = deep_queue.get(doi)
        result = fetch_crossref_work(
            doi,
            user_agent=str(config.get("user_agent")),
            timeout_seconds=int(request_config.get("timeout_seconds", 30)),
            retries=int(request_config.get("retries", 3)),
            backoff_seconds=list(request_config.get("backoff_seconds", [0, 30, 90])),
            mailto=config.get("crossref_mailto") or os.getenv("CROSSREF_MAILTO"),
        )
        run["counts"]["attempted"] += 1
        run["attempted_dois"].append({"doi": doi, "retry_day": due_day, "status": result.status})
        record["last_enrichment_attempt_at"] = checked_at
        record["crossref_enrichment_attempts"] = int(record.get("crossref_enrichment_attempts") or 0) + 1
        if queue_record is not None:
            queue_record["last_enrichment_attempt_at"] = checked_at

        if result.status != "success" or result.item is None:
            run["counts"]["failed"] += 1
            failure = {"doi": doi, "retry_day": due_day, "error": result.error, "http_status": result.http_status}
            run["failures"].append(failure)
            record["last_enrichment_error"] = result.error
            if queue_record is not None:
                queue_record["last_enrichment_error"] = result.error
            continue

        for key, value in crossref_work_metadata(result.item).items():
            if value not in (None, "", []) and (key == "summary_rss" or not record.get(key)):
                record[key] = value
        completed = completed_retry_days(record)
        completed.add(due_day)
        record["crossref_enrichment_completed_days"] = sorted(completed)
        record["last_enrichment_error"] = None

        if substantive_text(record):
            promote_evidence(
                record,
                queue_record,
                checked_at=checked_at,
                reason=f"crossref_abstract_enriched_d{due_day}",
            )
            run["counts"]["enriched_from_crossref"] += 1
            run["enriched_dois"].append({"doi": doi, "retry_day": due_day})
            continue

        run["counts"]["still_metadata_only"] += 1
        if retry_days and all(day in completed for day in retry_days):
            mark_exhausted(record, queue_record, checked_at)
            run["counts"]["exhausted"] += 1
            run["exhausted_dois"].append(doi)
        else:
            mark_waiting(record, queue_record, retry_days=retry_days, timezone_name=timezone_name)

    if run["counts"]["failed"] and run["counts"]["attempted"] == run["counts"]["failed"]:
        run["status"] = "failed"
    elif run["counts"]["failed"]:
        run["status"] = "partial_success"
    else:
        run["status"] = "success"
    run["completed_at"] = now_iso(timezone_name)

    atomic_write_json(doi_path, doi_index)
    atomic_write_json(queue_path, deep_queue)
    atomic_write_json(workspace / "public" / "crossref_enrichment_retry.json", run)
    run_path = workspace / "runs" / "enrichment" / checked_at[:4] / checked_at[5:7] / f"crossref-retry-{checked_at.replace(':', '').replace('+', 'p')}.json"
    atomic_write_json(run_path, run)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 1 if run["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
