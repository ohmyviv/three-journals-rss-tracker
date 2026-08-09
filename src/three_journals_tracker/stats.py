from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser
from zoneinfo import ZoneInfo


def build_feed_statistics(observations: list[dict[str, Any]], timezone_name: str) -> dict[str, Any]:
    tz = ZoneInfo(timezone_name)
    by_feed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_feed[str(row.get("feed_id", "unknown"))].append(row)

    result: dict[str, Any] = {
        "generated_at": datetime.now(tz).replace(microsecond=0).isoformat(),
        "timezone": timezone_name,
        "feeds": {},
    }
    for feed_id, rows in sorted(by_feed.items()):
        successful = [row for row in rows if row.get("status") in {"success", "not_modified", "fallback_success"}]
        changed = [row for row in successful if int(row.get("new_item_count") or 0) > 0]
        hour_counts: Counter[str] = Counter()
        weekday_counts: Counter[str] = Counter()
        for row in changed:
            checked_at = row.get("checked_at")
            if not checked_at:
                continue
            dt = date_parser.isoparse(checked_at).astimezone(tz)
            hour_counts[f"{dt.hour:02d}:00-{dt.hour:02d}:59"] += 1
            weekday_counts[dt.strftime("%A")] += 1
        result["feeds"][feed_id] = {
            "checks": len(rows),
            "successful_checks": len(successful),
            "failed_checks": len(rows) - len(successful),
            "checks_with_new_items": len(changed),
            "total_new_items_observed": sum(int(row.get("new_item_count") or 0) for row in changed),
            "new_item_checks_by_hour": dict(hour_counts.most_common()),
            "new_item_checks_by_weekday": dict(weekday_counts.most_common()),
            "last_checked_at": max((row.get("checked_at") for row in rows if row.get("checked_at")), default=None),
            "last_success_at": max((row.get("checked_at") for row in successful if row.get("checked_at")), default=None),
        }
    return result
