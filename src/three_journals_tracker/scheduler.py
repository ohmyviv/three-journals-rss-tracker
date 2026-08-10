from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from .io_utils import append_jsonl, atomic_write_json, read_jsonl


def _parse_values(text: str, minimum: int, maximum: int) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        value = int(part)
        if value < minimum or value > maximum:
            raise ValueError(f"Cron value {value} outside {minimum}-{maximum}")
        values.append(value)
    return sorted(set(values))


def scheduled_for_from_cron(triggered_at: str, cron_expression: str) -> datetime:
    """Resolve the most recent daily UTC cron occurrence at or before triggered_at.

    The tracker intentionally uses simple five-field daily schedules with explicit
    minute/hour values and wildcards for day, month, and weekday.
    """

    fields = cron_expression.split()
    if len(fields) != 5:
        raise ValueError(f"Expected five-field cron expression, got: {cron_expression!r}")
    minute_text, hour_text, day_text, month_text, weekday_text = fields
    if (day_text, month_text, weekday_text) != ("*", "*", "*"):
        raise ValueError(f"Only daily wildcard cron expressions are supported: {cron_expression!r}")

    minutes = _parse_values(minute_text, 0, 59)
    hours = _parse_values(hour_text, 0, 23)
    triggered = date_parser.isoparse(triggered_at).astimezone(timezone.utc)
    candidates: list[datetime] = []
    for day_offset in (0, -1):
        candidate_date = (triggered + timedelta(days=day_offset)).date()
        for hour in hours:
            for minute in minutes:
                candidate = datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    minute,
                    tzinfo=timezone.utc,
                )
                if candidate <= triggered:
                    candidates.append(candidate)
    if not candidates:
        raise ValueError(f"Could not resolve cron occurrence for {cron_expression!r}")
    return max(candidates)


def build_scheduler_event(
    *,
    workflow: str,
    triggered_at: str,
    trigger_type: str,
    schedule_expression: str | None,
    timezone_name: str,
    delay_threshold_minutes: int = 15,
) -> dict[str, Any]:
    triggered = date_parser.isoparse(triggered_at).astimezone(ZoneInfo(timezone_name))
    scheduled_for: str | None = None
    delay_minutes: float | None = None
    status = "non_scheduled"

    if trigger_type == "schedule" and schedule_expression:
        scheduled_utc = scheduled_for_from_cron(triggered_at, schedule_expression)
        scheduled_local = scheduled_utc.astimezone(ZoneInfo(timezone_name))
        scheduled_for = scheduled_local.replace(microsecond=0).isoformat()
        delay_minutes = round(max(0.0, (triggered - scheduled_local).total_seconds() / 60), 1)
        status = "scheduler_delayed" if delay_minutes > delay_threshold_minutes else "on_time"

    return {
        "workflow": workflow,
        "trigger_type": trigger_type,
        "schedule_expression_utc": schedule_expression or None,
        "scheduled_for": scheduled_for,
        "triggered_at": triggered.replace(microsecond=0).isoformat(),
        "delay_minutes": delay_minutes,
        "delay_threshold_minutes": delay_threshold_minutes,
        "scheduler_delayed": status == "scheduler_delayed",
        "status": status,
    }


def late_recovery_context(
    scheduler_event: dict[str, Any],
    *,
    cutoff_time: str,
    timezone_name: str,
) -> dict[str, Any]:
    scheduled_text = scheduler_event.get("scheduled_for")
    triggered_text = scheduler_event.get("triggered_at")
    if not scheduled_text or not triggered_text:
        return {"late_discovery_recovery": False, "intended_batch_date": None}

    tz = ZoneInfo(timezone_name)
    scheduled = date_parser.isoparse(str(scheduled_text)).astimezone(tz)
    triggered = date_parser.isoparse(str(triggered_text)).astimezone(tz)
    cutoff_hour, cutoff_minute = [int(part) for part in cutoff_time.split(":", 1)]
    cutoff = scheduled.replace(hour=cutoff_hour, minute=cutoff_minute, second=0, microsecond=0)
    is_recovery = scheduled <= cutoff < triggered
    return {
        "late_discovery_recovery": is_recovery,
        "intended_batch_date": scheduled.date().isoformat() if is_recovery else None,
    }


def write_scheduler_event(workspace: Path, event: dict[str, Any]) -> None:
    events_path = workspace / "data" / "scheduler_events.jsonl"
    append_jsonl(events_path, [event])
    rows = read_jsonl(events_path)
    workflows: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("workflow") or "unknown")
        summary = workflows.setdefault(
            name,
            {
                "runs": 0,
                "scheduled_runs": 0,
                "delayed_runs": 0,
                "max_delay_minutes": 0.0,
                "latest_event": None,
            },
        )
        summary["runs"] += 1
        if row.get("scheduled_for"):
            summary["scheduled_runs"] += 1
        if row.get("scheduler_delayed"):
            summary["delayed_runs"] += 1
        delay = row.get("delay_minutes")
        if isinstance(delay, (int, float)):
            summary["max_delay_minutes"] = max(float(summary["max_delay_minutes"]), float(delay))
        summary["latest_event"] = row

    latest = rows[-1] if rows else None
    latest_scheduled_by_workflow: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("scheduled_for"):
            name = str(row.get("workflow") or "unknown")
            latest_scheduled_by_workflow[name] = row

    scheduler_delayed = any(
        row.get("scheduler_delayed")
        for row in latest_scheduled_by_workflow.values()
    )
    atomic_write_json(
        workspace / "public" / "scheduler_health.json",
        {
            "generated_at": event.get("completed_at") or event.get("triggered_at"),
            "status": "scheduler_delayed" if scheduler_delayed else "ok",
            "total_events": len(rows),
            "latest_event": latest,
            "workflows": workflows,
        },
    )
