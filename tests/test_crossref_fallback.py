from three_journals_tracker.crossref_client import calculate_query_window, crossref_item_to_entry
from three_journals_tracker.model import entry_to_record


def test_crossref_item_maps_to_record():
    item = {
        "DOI": "10.1126/science.example",
        "title": ["A Science result"],
        "URL": "https://doi.org/10.1126/science.example",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "published-online": {"date-parts": [[2026, 7, 27]]},
        "created": {"date-time": "2026-07-28T02:00:00Z"},
        "subject": ["Neuroscience"],
        "container-title": ["Science"],
        "type": "journal-article",
    }
    entry = crossref_item_to_entry(item)
    record = entry_to_record(entry, "science", "Science", "2026-07-28T10:30:00+08:00", "Asia/Shanghai")
    assert record["doi"] == "10.1126/science.example"
    assert record["title"] == "A Science result"
    assert record["authors_rss"] == ["Ada Lovelace"]
    assert record["rss_reported_time"].startswith("2026-07-27")


def test_unseeded_journal_uses_publication_bootstrap_window():
    mode, start, discovery_mode = calculate_query_window(
        checked_at="2026-07-28T10:30:00+08:00",
        fallback_last_success_at=None,
        has_journal_history=False,
        bootstrap_lookback_days=60,
        initial_live_lookback_days=14,
        overlap_hours=48,
    )
    assert mode == "published"
    assert start == "2026-05-29"
    assert discovery_mode == "bootstrap"


def test_seeded_journal_uses_created_live_window():
    mode, start, discovery_mode = calculate_query_window(
        checked_at="2026-07-28T10:30:00+08:00",
        fallback_last_success_at="2026-07-28T06:30:00+08:00",
        has_journal_history=True,
        bootstrap_lookback_days=60,
        initial_live_lookback_days=14,
        overlap_hours=48,
    )
    assert mode == "created"
    assert start == "2026-07-25"
    assert discovery_mode == "live"


def test_crossref_fetch_deduplicates_issns_and_normalizes_filter_date(monkeypatch):
    from three_journals_tracker import crossref_client

    captured_filters = []

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "message": {
                    "items": [{"DOI": "10.1016/j.cell.test", "title": ["Test"]}],
                    "next-cursor": "next",
                }
            }

    def fake_get(*args, **kwargs):
        captured_filters.append(kwargs["params"]["filter"])
        return Response()

    monkeypatch.setattr(crossref_client.requests, "get", fake_get)
    result = crossref_client.fetch_crossref_works(
        issns=["0092-8674", "1097-4172"],
        query_mode="created",
        query_start="2026-05-01T12:34:56+00:00",
        user_agent="test-agent",
        timeout_seconds=1,
        retries=1,
        backoff_seconds=[0],
        rows=1000,
        max_pages=1,
    )
    assert result.status == "success"
    assert len(result.items) == 1
    assert result.attempts == 2
    assert result.query_start == "2026-05-01"
    assert captured_filters == [
        "type:journal-article,from-created-date:2026-05-01",
        "type:journal-article,from-created-date:2026-05-01",
    ]


def test_crossref_partial_issn_failure_with_zero_items_is_still_success(monkeypatch):
    from three_journals_tracker import crossref_client

    class SuccessResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "message": {
                    "items": [],
                    "next-cursor": None,
                }
            }

    class NotFoundResponse:
        status_code = 404
        text = "Resource not found."

        @staticmethod
        def json():
            return {}

    def fake_get(url, **kwargs):
        if "/journals/0092-8674/works" in url:
            return SuccessResponse()
        if "/journals/1097-4172/works" in url:
            return NotFoundResponse()
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(crossref_client.requests, "get", fake_get)

    result = crossref_client.fetch_crossref_works(
        issns=["0092-8674", "1097-4172"],
        query_mode="created",
        query_start="2026-08-07",
        user_agent="test-agent",
        timeout_seconds=1,
        retries=1,
        backoff_seconds=[0],
        rows=1000,
        max_pages=1,
    )

    assert result.status == "success"
    assert result.http_status == 200
    assert result.items == []
    assert result.attempts == 2
    assert "1097-4172" in (result.error or "")
    assert "HTTP 404" in (result.error or "")
