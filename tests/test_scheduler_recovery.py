import json
from datetime import datetime
from zoneinfo import ZoneInfo

from three_journals_tracker.batching import eligible_for_batch, has_new_late_recovery_items
from three_journals_tracker.scheduler import (
    build_scheduler_event,
    late_recovery_context,
    scheduled_for_from_cron,
    write_scheduler_event,
)


def test_explicit_utc_cron_resolves_beijing_schedule_and_delay():
    event = build_scheduler_event(
        workflow="rss_discovery",
        triggered_at="2026-07-29T13:34:52+08:00",
        trigger_type="schedule",
        schedule_expression="30 2 * * *",
        timezone_name="Asia/Shanghai",
        delay_threshold_minutes=15,
    )
    assert event["scheduled_for"] == "2026-07-29T10:30:00+08:00"
    assert event["delay_minutes"] == 184.9
    assert event["scheduler_delayed"] is True
    assert event["status"] == "scheduler_delayed"


def test_previous_utc_date_maps_to_same_beijing_morning():
    scheduled = scheduled_for_from_cron(
        "2026-07-29T07:32:00+08:00",
        "30 22 * * *",
    )
    assert scheduled.isoformat() == "2026-07-28T22:30:00+00:00"
    assert scheduled.astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == "2026-07-29T06:30:00+08:00"


def test_delayed_pre_cutoff_discovery_gets_recovery_context():
    event = build_scheduler_event(
        workflow="rss_discovery",
        triggered_at="2026-07-29T13:34:52+08:00",
        trigger_type="schedule",
        schedule_expression="30 2 * * *",
        timezone_name="Asia/Shanghai",
    )
    recovery = late_recovery_context(
        event,
        cutoff_time="10:50",
        timezone_name="Asia/Shanghai",
    )
    assert recovery == {
        "late_discovery_recovery": True,
        "intended_batch_date": "2026-07-29",
    }


def test_crossing_cutoff_recovers_even_below_delay_alert_threshold():
    event = build_scheduler_event(
        workflow="rss_discovery",
        triggered_at="2026-07-29T10:51:00+08:00",
        trigger_type="schedule",
        schedule_expression="37 2 * * *",
        timezone_name="Asia/Shanghai",
        delay_threshold_minutes=15,
    )
    assert event["delay_minutes"] == 14.0
    assert event["scheduler_delayed"] is False
    recovery = late_recovery_context(
        event,
        cutoff_time="10:50",
        timezone_name="Asia/Shanghai",
    )
    assert recovery == {
        "late_discovery_recovery": True,
        "intended_batch_date": "2026-07-29",
    }


def test_delayed_post_cutoff_discovery_is_not_recovered_into_morning_batch():
    event = build_scheduler_event(
        workflow="rss_discovery",
        triggered_at="2026-07-29T17:19:26+08:00",
        trigger_type="schedule",
        schedule_expression="30 6 * * *",
        timezone_name="Asia/Shanghai",
    )
    recovery = late_recovery_context(
        event,
        cutoff_time="10:50",
        timezone_name="Asia/Shanghai",
    )
    assert recovery["late_discovery_recovery"] is False
    assert recovery["intended_batch_date"] is None


def test_late_recovery_record_is_eligible_after_cutoff():
    cutoff = datetime(2026, 7, 29, 10, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
    record = {
        "discovery_status": "live_discovery",
        "batch_id": None,
        "first_seen_at": "2026-07-29T13:34:52+08:00",
        "late_discovery_recovery": True,
        "intended_batch_date": "2026-07-29",
    }
    assert eligible_for_batch(
        record,
        batch_id="daily-2026-07-29",
        target_date="2026-07-29",
        cutoff_at=cutoff,
        timezone_name="Asia/Shanghai",
    ) == (True, True)


def test_normal_post_cutoff_record_waits_for_next_batch():
    cutoff = datetime(2026, 7, 29, 10, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
    record = {
        "discovery_status": "live_discovery",
        "batch_id": None,
        "first_seen_at": "2026-07-29T14:31:00+08:00",
        "late_discovery_recovery": False,
        "intended_batch_date": None,
    }
    assert eligible_for_batch(
        record,
        batch_id="daily-2026-07-29",
        target_date="2026-07-29",
        cutoff_at=cutoff,
        timezone_name="Asia/Shanghai",
    ) == (False, False)


def test_recovery_rebuild_only_when_new_recovery_key_is_missing_from_batch():
    cutoff = datetime(2026, 7, 29, 10, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
    records = {
        "10.1000/recovered": {
            "discovery_status": "live_discovery",
            "batch_id": None,
            "first_seen_at": "2026-07-29T13:34:52+08:00",
            "late_discovery_recovery": True,
            "intended_batch_date": "2026-07-29",
        }
    }
    common = {
        "batch_id": "daily-2026-07-29",
        "target_date": "2026-07-29",
        "cutoff_at": cutoff,
        "timezone_name": "Asia/Shanghai",
    }
    assert has_new_late_recovery_items(records, existing_keys=set(), **common) is True
    assert has_new_late_recovery_items(records, existing_keys={"10.1000/recovered"}, **common) is False


def test_non_scheduled_event_does_not_clear_latest_scheduled_delay(tmp_path):
    delayed_discovery = {
        "workflow": "rss_discovery",
        "trigger_type": "schedule",
        "scheduled_for": "2026-08-10T06:30:00+08:00",
        "triggered_at": "2026-08-10T07:00:27+08:00",
        "delay_minutes": 30.4,
        "scheduler_delayed": True,
        "status": "scheduler_delayed",
        "completed_at": "2026-08-10T07:00:30+08:00",
    }
    normal_freeze = {
        "workflow": "freeze_daily_batch",
        "trigger_type": "workflow_run",
        "scheduled_for": None,
        "triggered_at": "2026-08-10T07:00:50+08:00",
        "delay_minutes": None,
        "scheduler_delayed": False,
        "status": "non_scheduled",
        "completed_at": "2026-08-10T07:00:50+08:00",
    }

    write_scheduler_event(tmp_path, delayed_discovery)
    write_scheduler_event(tmp_path, normal_freeze)

    health = json.loads(
        (tmp_path / "public" / "scheduler_health.json").read_text()
    )

    assert health["status"] == "scheduler_delayed"


def test_on_time_scheduled_event_from_other_workflow_does_not_clear_delay(tmp_path):
    delayed_discovery = {
        "workflow": "rss_discovery",
        "trigger_type": "schedule",
        "scheduled_for": "2026-08-10T06:30:00+08:00",
        "triggered_at": "2026-08-10T07:00:27+08:00",
        "delay_minutes": 30.4,
        "scheduler_delayed": True,
        "status": "scheduler_delayed",
        "completed_at": "2026-08-10T07:00:30+08:00",
    }
    on_time_europe_pmc = {
        "workflow": "europe_pmc_audit",
        "trigger_type": "schedule",
        "scheduled_for": "2026-08-10T07:45:00+08:00",
        "triggered_at": "2026-08-10T07:46:00+08:00",
        "delay_minutes": 1.0,
        "scheduler_delayed": False,
        "status": "ok",
        "completed_at": "2026-08-10T07:46:05+08:00",
    }

    write_scheduler_event(tmp_path, delayed_discovery)
    write_scheduler_event(tmp_path, on_time_europe_pmc)

    health = json.loads(
        (tmp_path / "public" / "scheduler_health.json").read_text()
    )

    assert health["status"] == "scheduler_delayed"


def test_on_time_scheduled_run_clears_previous_delay_for_same_workflow(tmp_path):
    delayed_discovery = {
        "workflow": "rss_discovery",
        "trigger_type": "schedule",
        "scheduled_for": "2026-08-10T06:30:00+08:00",
        "triggered_at": "2026-08-10T07:00:27+08:00",
        "delay_minutes": 30.4,
        "scheduler_delayed": True,
        "status": "scheduler_delayed",
        "completed_at": "2026-08-10T07:00:30+08:00",
    }
    on_time_discovery = {
        "workflow": "rss_discovery",
        "trigger_type": "schedule",
        "scheduled_for": "2026-08-10T08:47:00+08:00",
        "triggered_at": "2026-08-10T08:48:00+08:00",
        "delay_minutes": 1.0,
        "scheduler_delayed": False,
        "status": "ok",
        "completed_at": "2026-08-10T08:48:05+08:00",
    }

    write_scheduler_event(tmp_path, delayed_discovery)
    write_scheduler_event(tmp_path, on_time_discovery)

    health = json.loads(
        (tmp_path / "public" / "scheduler_health.json").read_text()
    )

    assert health["status"] == "ok"
