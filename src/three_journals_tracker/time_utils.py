from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser


def now_iso(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).replace(microsecond=0).isoformat()


def parse_any_datetime(value: object, timezone_name: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = date_parser.parse(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name)).replace(microsecond=0).isoformat()


def local_date(iso_value: str, timezone_name: str) -> str:
    return date_parser.isoparse(iso_value).astimezone(ZoneInfo(timezone_name)).date().isoformat()
