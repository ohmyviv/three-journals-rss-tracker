#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from three_journals_tracker.analysis_queue import active_backlog_count
from three_journals_tracker.discovery_records import journal_has_history, process_entries
from three_journals_tracker.discovery_sources import collect_source
from three_journals_tracker.enrichment_retry import crossref_work_metadata, fetch_crossref_work
from three_journals_tracker.io_utils import append_jsonl, atomic_write_json, read_json
from three_journals_tracker.scheduler import build_scheduler_event, late_recovery_context, write_scheduler_event
from three_journals_tracker.stats import build_feed_statistics
from three_journals_tracker.time_utils import now_iso


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Nature, Science, and Cell items with source fallbacks")
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
    config, feeds_config = load_yaml(args.config), load_yaml(args.feeds)
    timezone_name = str(config.get("timezone", "Asia/Shanghai"))
    if args.now:
        checked_at = date_parser.isoparse(args.now).astimezone(ZoneInfo(timezone_name)).replace(microsecond=0).isoformat()
    else:
        checked_at = now_iso(timezone_name)
    run_id = "discover-" + checked_at.replace(":", "").replace("+", "p")

    scheduler_config = config.get("scheduler", {})
    scheduler_event = build_scheduler_event(
        workflow="rss_discovery",
        triggered_at=checked_at,
        trigger_type=str(args.trigger_type or "manual"),
        schedule_expression=str(args.schedule_expression or "") or None,
        timezone_name=timezone_name,
        delay_threshold_minutes=int(scheduler_config.get("delay_threshold_minutes", 15)),
    )
    recovery = late_recovery_context(
        scheduler_event,
        cutoff_time=str(config.get("batch", {}).get("cutoff_time", "12:17")),
        timezone_name=timezone_name,
    )
    scheduler_metadata = {
        "scheduled_for": scheduler_event.get("scheduled_for"),
        "triggered_at": scheduler_event.get("triggered_at"),
        "scheduler_delay_minutes": scheduler_event.get("delay_minutes"),
        "scheduler_delayed": scheduler_event.get("scheduler_delayed", False),
        **recovery,
    }

    paths = {
        "doi": workspace / "data" / "doi_index.json",
        "pending": workspace / "data" / "pending_doi.json",
        "source": workspace / "data" / "source_state.json",
        "queue": workspace / "data" / "deep_analysis_queue.json",
    }
    doi_index = read_json(paths["doi"], {})
    pending_doi = read_json(paths["pending"], {})
    source_state = read_json(paths["source"], {})
    deep_queue = read_json(paths["queue"], {})
    mode = args.mode
    if mode == "auto":
        mode = "bootstrap" if not doi_index and not pending_doi else "live"

    run: dict[str, Any] = {
        "schema_version": config.get("schema_version", "1.0"),
        "run_id": run_id,
        "mode": mode,
        "checked_at": checked_at,
        "timezone": timezone_name,
        "status": "running",
        "trigger_type": scheduler_event["trigger_type"],
        "schedule_expression_utc": scheduler_event["schedule_expression_utc"],
        "scheduled_for": scheduler_event["scheduled_for"],
        "triggered_at": scheduler_event["triggered_at"],
        "scheduler_delay_minutes": scheduler_event["delay_minutes"],
        "scheduler_delayed": scheduler_event["scheduler_delayed"],
        "late_discovery_recovery": recovery["late_discovery_recovery"],
        "intended_batch_date": recovery["intended_batch_date"],
        "source_status": {},
        "counts": {
            "new_dois": 0,
            "new_pending_doi": 0,
            "seed_dois": 0,
            "seed_pending_doi": 0,
            "resolved_dois": 0,
            "duplicates": 0,
            "items_seen": 0,
            "late_recovery_dois": 0,
            "late_recovery_pending_doi": 0,
            "china_team_lookup_attempted": 0,
            "china_team_lookup_enriched": 0,
            "china_team_lookup_failed": 0,
            "china_team_unknown": 0,
        },
        "new_dois": [],
        "new_pending_keys": [],
        "seed_dois": [],
        "seed_pending_keys": [],
    }
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    successful_sources = 0

    enabled_feeds = [feed for feed in feeds_config.get("feeds", []) if feed.get("enabled", True)]
    for feed in enabled_feeds:
        feed_id, journal = str(feed["id"]), str(feed["journal"])
        previous = source_state.get(feed_id, {})
        result = collect_source(
            feed=feed,
            config=config,
            checked_at=checked_at,
            mode=mode,
            previous=previous,
            has_history=journal_has_history(journal, doi_index, pending_doi),
        )
        observation = {
            "run_id": run_id,
            **result["observation"],
            "scheduled_for": scheduler_event.get("scheduled_for"),
            "triggered_at": scheduler_event.get("triggered_at"),
            "scheduler_delay_minutes": scheduler_event.get("delay_minutes"),
            "scheduler_delayed": scheduler_event.get("scheduler_delayed", False),
        }
        run["source_status"][feed_id] = result["source_status"]
        source_state[feed_id] = result["state"]
        if result["success"]:
            successful_sources += 1
            counts = process_entries(
                entries=result["entries"],
                feed_id=feed_id,
                journal=journal,
                checked_at=checked_at,
                timezone_name=timezone_name,
                run_id=run_id,
                discovery_source=result["source"],
                discovery_mode=result["mode"],
                previous_success_at=previous.get("last_success_at"),
                doi_index=doi_index,
                pending_doi=pending_doi,
                deep_queue=deep_queue,
                run=run,
                events=events,
                scheduler_metadata=scheduler_metadata,
            )
            observation["new_item_count"], observation["new_doi_count"], observation["new_pending_count"] = counts
        observations.append(observation)

    # Lightweight affiliation enrichment for newly discovered live DOI records only.
    # This lets Nature RSS discoveries receive the same optional China-team hint as
    # Crossref/Europe-PMC records without turning team identity into a discovery or
    # editorial ranking dependency. Failures are non-blocking and remain `unknown`.
    request_config = config.get("request", {})
    china_fields = (
        "author_affiliations",
        "affiliations",
        "china_team_status",
        "china_institutions",
        "china_key_authors",
        "china_team_evidence",
    )
    for doi in list(run["new_dois"]):
        record = doi_index.get(doi)
        if not record or record.get("china_team_status") != "unknown":
            continue
        run["counts"]["china_team_lookup_attempted"] += 1
        result = fetch_crossref_work(
            doi,
            user_agent=str(config.get("user_agent")),
            timeout_seconds=int(request_config.get("timeout_seconds", 30)),
            retries=int(request_config.get("retries", 3)),
            backoff_seconds=list(request_config.get("backoff_seconds", [0, 30, 90])),
            mailto=config.get("crossref_mailto") or os.getenv("CROSSREF_MAILTO"),
        )
        if result.status != "success" or result.item is None:
            run["counts"]["china_team_lookup_failed"] += 1
            continue
        metadata = crossref_work_metadata(result.item)
        for key in china_fields:
            value = metadata.get(key)
            if value not in (None, "", []):
                record[key] = value
        record["last_updated_at"] = checked_at
        if record.get("china_team_status") != "unknown":
            run["counts"]["china_team_lookup_enriched"] += 1

    run["counts"]["china_team_unknown"] = sum(
        1 for doi in run["new_dois"] if doi_index.get(doi, {}).get("china_team_status") == "unknown"
    )

    fallback_used = any(value == "fallback_crossref" for value in run["source_status"].values())
    if successful_sources == 0:
        run["status"] = "failed_all_sources"
    elif successful_sources < len(enabled_feeds):
        run["status"] = "partial_success"
    elif fallback_used:
        run["status"] = "degraded_fallback_sources"
    elif run["counts"]["new_dois"] or run["counts"]["new_pending_doi"]:
        run["status"] = "success_new_items"
    elif run["counts"]["seed_dois"] or run["counts"]["seed_pending_doi"]:
        run["status"] = "success_seeded_items"
    else:
        run["status"] = "success_zero_new"

    run["completed_at"] = now_iso(timezone_name)
    append_jsonl(workspace / "data" / "discovery_events.jsonl", events)
    append_jsonl(workspace / "data" / "feed_observations.jsonl", observations)
    atomic_write_json(paths["doi"], doi_index)
    atomic_write_json(paths["pending"], pending_doi)
    atomic_write_json(paths["source"], source_state)
    # Discovery deliberately does not rewrite deep_analysis_queue.json. Deep-analysis
    # state is owned by the explicit editorial decision/writeback step.
    atomic_write_json(workspace / "runs" / checked_at[:4] / checked_at[5:7] / f"{run_id}.json", run)
    atomic_write_json(workspace / "public" / "latest_run.json", run)

    observations_path = workspace / "data" / "feed_observations.jsonl"
    all_observations = []
    if observations_path.exists():
        all_observations = [json.loads(line) for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    atomic_write_json(workspace / "public" / "feed_statistics.json", build_feed_statistics(all_observations, timezone_name))
    atomic_write_json(workspace / "public" / "health.json", {
        "generated_at": run["completed_at"],
        "status": run["status"],
        "latest_run_id": run_id,
        "source_status": run["source_status"],
        "scheduler_delayed": run["scheduler_delayed"],
        "scheduler_delay_minutes": run["scheduler_delay_minutes"],
        "late_discovery_recovery": run["late_discovery_recovery"],
        "doi_index_count": len(doi_index),
        "pending_doi_count": len(pending_doi),
        "deep_analysis_queue_count": active_backlog_count(deep_queue),
        "deep_analysis_record_count": len(deep_queue),
    })
    write_scheduler_event(
        workspace,
        {
            **scheduler_event,
            **recovery,
            "run_id": run_id,
            "outcome": run["status"],
            "completed_at": run["completed_at"],
        },
    )
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 1 if run["status"] == "failed_all_sources" else 0


if __name__ == "__main__":
    raise SystemExit(main())
