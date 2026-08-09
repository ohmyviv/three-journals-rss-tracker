from datetime import datetime
from zoneinfo import ZoneInfo

from three_journals_tracker.enrichment_retry import (
    formal_batch_fields,
    next_retry_at,
    retry_day_due,
    substantive_text,
)


def test_retry_schedule_uses_local_calendar_days():
    record = {
        "first_seen_at": "2026-08-01T07:30:00+08:00",
        "crossref_enrichment_completed_days": [],
    }
    now_at = datetime(2026, 8, 4, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert retry_day_due(
        record,
        now_at=now_at,
        retry_days=[3, 7, 14],
        timezone_name="Asia/Shanghai",
    ) == 3


def test_retry_advances_after_completed_day():
    record = {
        "first_seen_at": "2026-08-01T07:30:00+08:00",
        "crossref_enrichment_completed_days": [3],
    }
    day_six = datetime(2026, 8, 7, 22, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    day_seven = datetime(2026, 8, 8, 22, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert retry_day_due(
        record,
        now_at=day_six,
        retry_days=[3, 7, 14],
        timezone_name="Asia/Shanghai",
    ) is None
    assert retry_day_due(
        record,
        now_at=day_seven,
        retry_days=[3, 7, 14],
        timezone_name="Asia/Shanghai",
    ) == 7
    assert next_retry_at(
        record,
        retry_days=[3, 7, 14],
        timezone_name="Asia/Shanghai",
    ) == "2026-08-08T00:00:00+08:00"


def test_formal_batch_fields_are_derived_from_assigned_batch():
    assert formal_batch_fields({"batch_id": "daily-2026-07-31"}) == {
        "first_formal_batch_id": "daily-2026-07-31",
        "first_formal_batch_date": "2026-07-31",
    }
    assert formal_batch_fields({"batch_id": "bootstrap-2026-07-31"}) == {}


def test_substantive_text_accepts_europe_pmc_abstract_fallback():
    record = {
        "summary_rss": "",
        "abstract": "This is a sufficiently detailed abstract returned by Europe PMC for later analysis.",
    }
    assert substantive_text(record).startswith("This is a sufficiently detailed abstract")
