from datetime import datetime
from zoneinfo import ZoneInfo

from three_journals_tracker.enrichment_retry import (
    crossref_work_metadata,
    formal_batch_fields,
    merge_crossref_metadata,
    next_retry_at,
    queued_analysis_status_after_evidence,
    queued_analysis_status_while_waiting,
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


def test_crossref_work_metadata_contains_affiliation_china_hint():
    metadata = crossref_work_metadata(
        {
            "DOI": "10.1234/test",
            "title": ["Test paper"],
            "author": [
                {
                    "given": "Jane",
                    "family": "Doe",
                    "affiliation": [{"name": "Fudan University, Shanghai, China"}],
                }
            ],
        }
    )
    assert metadata["affiliations"] == ["Fudan University, Shanghai, China"]
    assert metadata["china_team_status"] == "china_led"
    assert metadata["china_key_authors"] == ["Jane Doe"]


def test_delayed_crossref_enrichment_can_upgrade_unknown_china_hint():
    record = {
        "china_team_status": "unknown",
        "china_institutions": [],
        "china_key_authors": [],
        "china_team_evidence": [],
    }
    merge_crossref_metadata(
        record,
        {
            "china_team_status": "china_participating",
            "china_institutions": ["Fudan University, Shanghai, China"],
            "china_key_authors": ["Jane Doe"],
            "china_team_evidence": ["crossref:author_affiliation"],
        },
    )
    assert record["china_team_status"] == "china_participating"
    assert record["china_institutions"] == ["Fudan University, Shanghai, China"]


def test_delayed_crossref_enrichment_does_not_downgrade_existing_positive_hint():
    record = {
        "china_team_status": "china_led",
        "china_institutions": ["Peking University, Beijing, China"],
    }
    merge_crossref_metadata(
        record,
        {
            "china_team_status": "no_china_signal",
            "china_institutions": [],
        },
    )
    assert record["china_team_status"] == "china_led"
    assert record["china_institutions"] == ["Peking University, Beijing, China"]


def test_enrichment_does_not_retriage_ready_or_capacity_deferred_work():
    assert queued_analysis_status_after_evidence(
        "pending",
        was_evidence_waiting=False,
    ) == "pending"
    assert queued_analysis_status_after_evidence(
        "deferred",
        was_evidence_waiting=False,
    ) == "deferred"


def test_new_evidence_promotes_only_evidence_waiting_work_to_pending():
    assert queued_analysis_status_after_evidence(
        "deferred",
        was_evidence_waiting=True,
    ) == "pending"
    assert queued_analysis_status_after_evidence(
        "awaiting_enrichment",
        was_evidence_waiting=True,
    ) == "pending"
    assert queued_analysis_status_after_evidence(
        "metadata_only_exhausted",
        was_evidence_waiting=True,
    ) == "pending"


def test_old_enrichment_generated_pending_triage_is_normalized_when_evidence_exists():
    assert queued_analysis_status_after_evidence(
        "pending_triage",
        was_evidence_waiting=False,
    ) == "pending"


def test_evidence_poor_queued_work_stays_active_as_deferred():
    for status in (
        None,
        "pending",
        "pending_triage",
        "deferred",
        "awaiting_enrichment",
        "metadata_only_exhausted",
    ):
        assert queued_analysis_status_while_waiting(status) == "deferred"
    assert queued_analysis_status_while_waiting("completed") == "completed"
