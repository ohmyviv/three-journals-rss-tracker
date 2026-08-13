#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from three_journals_tracker.batching import (
    cross_day_carryover_info,
    eligible_for_batch,
    has_new_late_recovery_items,
)
from three_journals_tracker.io_utils import atomic_write_json, read_json
from three_journals_tracker.scheduler import build_scheduler_event, write_scheduler_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze a daily ChatGPT input batch")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "tracker.yaml")
    parser.add_argument("--date", help="Local YYYY-MM-DD; defaults to today")
    parser.add_argument("--force", action="store_true", help="Replace an existing frozen batch")
    parser.add_argument(
        "--recover-late",
        action="store_true",
        help="Rebuild only when the latest delayed pre-cutoff discovery added eligible items",
    )
    parser.add_argument("--now", help="Override current timestamp (ISO 8601; mainly for tests and recovery)")
    parser.add_argument("--trigger-type", default=os.getenv("GITHUB_EVENT_NAME", "manual"))
    parser.add_argument("--schedule-expression", default=os.getenv("SCHEDULE_EXPRESSION", ""))
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    config = load_yaml(args.config)
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    tz = ZoneInfo(timezone_name)
    now_at = date_parser.isoparse(args.now).astimezone(tz) if args.now else datetime.now(tz)
    now_at = now_at.replace(microsecond=0)

    latest_run: dict[str, Any] = read_json(workspace / "public" / "latest_run.json", {})
    recovery_target = latest_run.get("intended_batch_date") if args.recover_late else None
    target_date = args.date or recovery_target or now_at.date().isoformat()
    cutoff_text = str(config.get("batch", {}).get("cutoff_time", "10:50"))
    cutoff_hour, cutoff_minute = [int(part) for part in cutoff_text.split(":", 1)]
    cutoff_at = datetime.combine(datetime.fromisoformat(target_date).date(), time(cutoff_hour, cutoff_minute), tzinfo=tz)
    batch_id = f"daily-{target_date}"
    batch_path = workspace / "public" / "batches" / f"{target_date}.json"
    existing_batch: dict[str, Any] = read_json(batch_path, {}) if batch_path.exists() else {}
    existing_generated_at = existing_batch.get("generated_at")
    existing_is_premature = bool(
        existing_generated_at
        and date_parser.isoparse(str(existing_generated_at)).astimezone(tz) < cutoff_at
    )

    scheduler_event = build_scheduler_event(
        workflow="freeze_daily_batch",
        triggered_at=now_at.isoformat(),
        trigger_type=str(args.trigger_type or "manual"),
        schedule_expression=str(args.schedule_expression or "") or None,
        timezone_name=timezone_name,
        delay_threshold_minutes=int(config.get("scheduler", {}).get("delay_threshold_minutes", 15)),
    )

    def finish(outcome: str, *, exit_code: int = 0, extra: dict[str, Any] | None = None) -> int:
        event = {
            **scheduler_event,
            "batch_id": batch_id,
            "target_date": target_date,
            "outcome": outcome,
            "completed_at": datetime.now(tz).replace(microsecond=0).isoformat(),
            **(extra or {}),
        }
        write_scheduler_event(workspace, event)
        print(json.dumps({"status": outcome, "batch_id": batch_id, **(extra or {})}, ensure_ascii=False))
        return exit_code

    if now_at < cutoff_at and not args.force:
        return finish(
            "too_early",
            exit_code=3,
            extra={
                "now_at": now_at.isoformat(),
                "cutoff_at": cutoff_at.isoformat(),
                "existing_premature_batch": bool(existing_batch and existing_is_premature),
            },
        )

    doi_index: dict[str, Any] = read_json(workspace / "data" / "doi_index.json", {})
    pending_doi: dict[str, Any] = read_json(workspace / "data" / "pending_doi.json", {})
    deep_queue: dict[str, Any] = read_json(workspace / "data" / "deep_analysis_queue.json", {})
    batch_index: dict[str, Any] = read_json(workspace / "data" / "batch_index.json", {})
    source_state: dict[str, Any] = read_json(workspace / "data" / "source_state.json", {})

    recovery_updated_batch = False
    if args.recover_late:
        latest_is_recovery = bool(
            latest_run.get("late_discovery_recovery")
            and latest_run.get("intended_batch_date") == target_date
        )
        if not latest_is_recovery:
            if existing_batch:
                atomic_write_json(workspace / "public" / "latest_batch.json", existing_batch)
            return finish("no_late_recovery_needed")

        existing_dois = {str(item.get("doi")) for item in existing_batch.get("new_items", []) if item.get("doi")}
        existing_pending = {
            str(item.get("temporary_key"))
            for item in existing_batch.get("missing_doi_items", [])
            if item.get("temporary_key")
        }
        has_new_recovery = has_new_late_recovery_items(
            doi_index,
            existing_keys=existing_dois,
            batch_id=batch_id,
            target_date=target_date,
            cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        ) or has_new_late_recovery_items(
            pending_doi,
            existing_keys=existing_pending,
            batch_id=batch_id,
            target_date=target_date,
            cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        )
        if existing_batch and not has_new_recovery:
            atomic_write_json(workspace / "public" / "latest_batch.json", existing_batch)
            return finish("checked_existing_no_new_recovery")
        recovery_updated_batch = bool(existing_batch and has_new_recovery)
    elif existing_batch and not args.force and not existing_is_premature:
        atomic_write_json(workspace / "public" / "latest_batch.json", existing_batch)
        return finish("checked_existing", extra={"path": str(batch_path)})

    generated_at = now_at.isoformat()
    new_items: list[dict[str, Any]] = []
    late_recovery_dois = 0
    cross_day_carryover_dois = 0
    for doi, record in doi_index.items():
        eligible, included_as_recovery = eligible_for_batch(
            record,
            batch_id=batch_id,
            target_date=target_date,
            cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        )
        if not eligible:
            continue
        is_carryover, carryover_from_date = cross_day_carryover_info(
            record,
            target_date=target_date,
            timezone_name=timezone_name,
        )
        if included_as_recovery:
            late_recovery_dois += 1
        if is_carryover:
            cross_day_carryover_dois += 1
        new_items.append({
            "doi": doi,
            "journal": record.get("journal"),
            "title": record.get("current_title"),
            "source_url": record.get("source_url"),
            "rss_reported_time": record.get("rss_reported_time"),
            "first_seen_at": record.get("first_seen_at"),
            "appearance_window_start": record.get("appearance_window_start"),
            "appearance_window_end": record.get("appearance_window_end"),
            "authors_rss": record.get("authors_rss", []),
            "tags_rss": record.get("tags_rss", []),
            "summary_rss": record.get("summary_rss"),
            "enrichment_status": record.get("enrichment_status", "pending"),
            "discovery_source": record.get("discovery_source"),
            "queue_status": deep_queue.get(doi, {}).get("analysis_status", "not_queued"),
            "included_as_late_recovery": included_as_recovery,
            "cross_day_carryover": is_carryover,
            "carryover_from_date": carryover_from_date,
            "intended_batch_date": record.get("intended_batch_date"),
            "scheduled_for": record.get("scheduled_for"),
            "scheduler_delay_minutes": record.get("scheduler_delay_minutes"),
        })

    pending_items: list[dict[str, Any]] = []
    late_recovery_pending = 0
    cross_day_carryover_pending = 0
    for key, record in pending_doi.items():
        eligible, included_as_recovery = eligible_for_batch(
            record,
            batch_id=batch_id,
            target_date=target_date,
            cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        )
        if not eligible:
            continue
        is_carryover, carryover_from_date = cross_day_carryover_info(
            record,
            target_date=target_date,
            timezone_name=timezone_name,
        )
        if included_as_recovery:
            late_recovery_pending += 1
        if is_carryover:
            cross_day_carryover_pending += 1
        pending_items.append({
            "temporary_key": key,
            "journal": record.get("journal"),
            "title": record.get("title"),
            "source_url": record.get("source_url"),
            "rss_reported_time": record.get("rss_reported_time"),
            "first_seen_at": record.get("first_seen_at"),
            "doi_status": "pending",
            "included_as_late_recovery": included_as_recovery,
            "cross_day_carryover": is_carryover,
            "carryover_from_date": carryover_from_date,
            "intended_batch_date": record.get("intended_batch_date"),
            "scheduled_for": record.get("scheduled_for"),
            "scheduler_delay_minutes": record.get("scheduler_delay_minutes"),
        })

    new_items.sort(key=lambda item: (item.get("journal") or "", item.get("first_seen_at") or "", item.get("title") or ""))
    pending_items.sort(key=lambda item: (item.get("journal") or "", item.get("first_seen_at") or "", item.get("title") or ""))

    backlog = [row for row in deep_queue.values() if row.get("analysis_status") in {"pending_triage", "pending", "deferred"}]
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "untriaged": 4}
    backlog.sort(key=lambda row: (
        priority_order.get(row.get("priority_level"), 9),
        -(row.get("priority_score") or -1),
        row.get("queued_at") or "",
    ))
    preview_limit = int(config.get("batch", {}).get("backlog_preview_limit", 30))

    source_status = dict(latest_run.get("source_status", {}))
    latest_run_status = latest_run.get("status")
    same_day_success: dict[str, bool] = {}
    for feed_id, state in source_state.items():
        last_success = state.get("last_success_at")
        same_day_success[feed_id] = bool(
            last_success
            and date_parser.isoparse(str(last_success)).astimezone(tz).date().isoformat() == target_date
        )
    if latest_run_status == "failed_all_sources":
        if any(same_day_success.values()):
            batch_status = "partial_sources"
            for feed_id, had_success in same_day_success.items():
                source_status[feed_id] = "stale_same_day_success" if had_success else "failed_no_same_day_success"
        else:
            batch_status = "blocked_failed_sources"
    elif latest_run_status == "partial_success":
        batch_status = "partial_sources"
    elif latest_run_status == "degraded_fallback_sources":
        batch_status = "degraded_sources"
    elif new_items or pending_items:
        batch_status = "success_new_items"
    else:
        batch_status = "success_zero_new"

    scheduler_delayed = bool(
        scheduler_event.get("scheduler_delayed")
        or latest_run.get("scheduler_delayed")
        or late_recovery_dois
        or late_recovery_pending
    )
    flags: list[str] = []
    if scheduler_delayed:
        flags.append("scheduler_delayed")
    if late_recovery_dois or late_recovery_pending:
        flags.append("late_discovery_recovery")
    if cross_day_carryover_dois or cross_day_carryover_pending:
        flags.append("cross_day_carryover")

    batch = {
        "schema_version": config.get("schema_version", "1.0"),
        "batch_id": batch_id,
        "date": target_date,
        "generated_at": generated_at,
        "cutoff_at": cutoff_at.replace(microsecond=0).isoformat(),
        "timezone": timezone_name,
        "status": batch_status,
        "flags": flags,
        "scheduler_status": "scheduler_delayed" if scheduler_delayed else "ok",
        "scheduler_delayed": scheduler_delayed,
        "freeze_trigger_type": scheduler_event.get("trigger_type"),
        "freeze_scheduled_for": scheduler_event.get("scheduled_for"),
        "freeze_triggered_at": scheduler_event.get("triggered_at"),
        "freeze_delay_minutes": scheduler_event.get("delay_minutes"),
        "replaced_premature_batch": bool(existing_batch and existing_is_premature),
        "recovery_updated_batch": recovery_updated_batch,
        "latest_discovery_run_id": latest_run.get("run_id"),
        "latest_discovery_checked_at": latest_run.get("checked_at"),
        "latest_discovery_scheduled_for": latest_run.get("scheduled_for"),
        "latest_discovery_delay_minutes": latest_run.get("scheduler_delay_minutes"),
        "source_status": source_status,
        "counts": {
            "new_dois": len(new_items),
            "new_missing_doi": len(pending_items),
            "late_recovery_dois": late_recovery_dois,
            "late_recovery_missing_doi": late_recovery_pending,
            "cross_day_carryover_dois": cross_day_carryover_dois,
            "cross_day_carryover_missing_doi": cross_day_carryover_pending,
            "deep_analysis_backlog": len(backlog),
            "backlog_previewed": min(len(backlog), preview_limit),
        },
        "deep_analysis_policy": {
            "daily_target": int(config.get("batch", {}).get("deep_analysis_daily_target", 15)),
            "hard_cap": int(config.get("batch", {}).get("deep_analysis_hard_cap", 20)),
            "zero_new_day_action": "Use capacity to process the oldest/highest-priority backlog items",
            "overflow_action": "Keep every new item in the full list; defer lower-priority deep analyses across later days",
        },
        "new_items": new_items,
        "missing_doi_items": pending_items,
        "deep_analysis_backlog": backlog[:preview_limit],
    }

    if batch_status != "blocked_failed_sources":
        for item in new_items:
            doi_index[item["doi"]]["batch_id"] = batch_id
            doi_index[item["doi"]]["batched_at"] = generated_at
        for item in pending_items:
            key = item["temporary_key"]
            pending_doi[key]["batch_id"] = batch_id
            pending_doi[key]["batched_at"] = generated_at
        batch_index[batch_id] = {
            "batch_id": batch_id,
            "date": target_date,
            "generated_at": generated_at,
            "status": batch_status,
            "flags": flags,
            "new_doi_count": len(new_items),
            "new_missing_doi_count": len(pending_items),
            "late_recovery_doi_count": late_recovery_dois,
            "cross_day_carryover_doi_count": cross_day_carryover_dois,
            "cross_day_carryover_missing_doi_count": cross_day_carryover_pending,
            "path": f"public/batches/{target_date}.json",
        }
        atomic_write_json(workspace / "data" / "doi_index.json", doi_index)
        atomic_write_json(workspace / "data" / "pending_doi.json", pending_doi)
        atomic_write_json(workspace / "data" / "batch_index.json", batch_index)

    atomic_write_json(batch_path, batch)
    atomic_write_json(workspace / "public" / "latest_batch.json", batch)
    write_scheduler_event(
        workspace,
        {
            **scheduler_event,
            "batch_id": batch_id,
            "target_date": target_date,
            "outcome": batch_status,
            "late_recovery_dois": late_recovery_dois,
            "cross_day_carryover_dois": cross_day_carryover_dois,
            "cross_day_carryover_missing_doi": cross_day_carryover_pending,
            "recovery_updated_batch": recovery_updated_batch,
            "completed_at": datetime.now(tz).replace(microsecond=0).isoformat(),
        },
    )
    print(json.dumps(batch, ensure_ascii=False, indent=2))
    return 2 if batch_status == "blocked_failed_sources" else 0


if __name__ == "__main__":
    raise SystemExit(main())
