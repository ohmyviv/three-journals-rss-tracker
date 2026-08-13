from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser


def eligible_for_batch(
    record: dict[str, Any],
    *,
    batch_id: str,
    target_date: str,
    cutoff_at: datetime,
    timezone_name: str,
) -> tuple[bool, bool]:
    """Return (eligible, included_as_late_recovery)."""

    assigned_batch_id = record.get("batch_id")
    if assigned_batch_id not in (None, batch_id):
        return False, False
    if record.get("discovery_status") != "live_discovery":
        return False, False

    first_seen_at = record.get("first_seen_at")
    if first_seen_at:
        first_seen = date_parser.isoparse(str(first_seen_at)).astimezone(ZoneInfo(timezone_name))
        if first_seen <= cutoff_at:
            return True, False

    is_recovery = bool(
        record.get("late_discovery_recovery")
        and record.get("intended_batch_date") == target_date
    )
    return is_recovery, is_recovery


def cross_day_carryover_info(
    record: dict[str, Any],
    *,
    target_date: str,
    timezone_name: str,
) -> tuple[bool, str | None]:
    """Return whether an unbatched record was first discovered on an earlier local date.

    This is intentionally separate from late-discovery recovery. A carryover item is a
    normal live discovery that missed the previous formal batch and is first admitted to
    a later day's batch. A late recovery item instead belongs to its explicit
    ``intended_batch_date`` even when the scheduler actually ran after that day's cutoff.
    """

    first_seen_at = record.get("first_seen_at")
    if not first_seen_at:
        return False, None
    first_seen = date_parser.isoparse(str(first_seen_at)).astimezone(ZoneInfo(timezone_name))
    first_seen_date = first_seen.date().isoformat()
    if first_seen_date < target_date:
        return True, first_seen_date
    return False, None


def has_new_late_recovery_items(
    records: dict[str, dict[str, Any]],
    *,
    existing_keys: set[str],
    batch_id: str,
    target_date: str,
    cutoff_at: datetime,
    timezone_name: str,
) -> bool:
    for key, record in records.items():
        eligible, is_recovery = eligible_for_batch(
            record,
            batch_id=batch_id,
            target_date=target_date,
            cutoff_at=cutoff_at,
            timezone_name=timezone_name,
        )
        if eligible and is_recovery and key not in existing_keys:
            return True
    return False
