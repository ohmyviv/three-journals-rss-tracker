from three_journals_tracker.stats import build_feed_statistics


def test_stats_count_changed_checks():
    rows = [
        {"feed_id": "nature", "checked_at": "2026-07-27T06:30:00+08:00", "status": "success", "new_item_count": 0},
        {"feed_id": "nature", "checked_at": "2026-07-27T10:30:00+08:00", "status": "success", "new_item_count": 3},
    ]
    result = build_feed_statistics(rows, "Asia/Shanghai")["feeds"]["nature"]
    assert result["checks"] == 2
    assert result["checks_with_new_items"] == 1
    assert result["total_new_items_observed"] == 3
