from pathlib import Path

from three_journals_tracker.feed_client import parse_feed
from three_journals_tracker.model import entry_to_record

FIXTURES = Path(__file__).parent / "fixtures"


def _record(name: str, feed_id: str, journal: str):
    _, entries, error = parse_feed((FIXTURES / name).read_bytes())
    assert entries
    return entry_to_record(entries[0], feed_id, journal, "2026-07-27T10:30:00+08:00", "Asia/Shanghai")


def test_nature_rdf_doi():
    assert _record("nature_rdf.xml", "nature", "Nature")["doi"] == "10.1038/s41586-026-00001-2"


def test_science_rss_doi_from_guid():
    assert _record("science_rss.xml", "science", "Science")["doi"] == "10.1126/science.abc1234"


def test_cell_rss_doi_from_description():
    assert _record("cell_rss.xml", "cell", "Cell")["doi"] == "10.1016/j.cell.2026.01.001"
