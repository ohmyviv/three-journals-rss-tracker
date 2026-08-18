from three_journals_tracker.europe_pmc_client import (
    europe_pmc_item_to_entry,
    europe_pmc_metadata,
    item_matches_journal,
)
from three_journals_tracker.model import entry_to_record


def _item():
    return {
        "id": "12345678",
        "source": "MED",
        "pmid": "12345678",
        "pmcid": "PMC123",
        "doi": "10.1126/science.abc1234",
        "title": "A test Science paper",
        "firstPublicationDate": "2026-07-25",
        "abstractText": "<p>Important abstract.</p>",
        "journalInfo": {
            "journal": {"title": "Science", "issn": "0036-8075", "essn": "1095-9203"}
        },
        "authorList": {
            "author": [
                {
                    "fullName": "Jane Doe",
                    "authorAffiliationDetailsList": {
                        "authorAffiliation": [{"affiliation": "Peking University, Beijing, China"}]
                    },
                }
            ]
        },
        "pubTypeList": {"pubType": ["Journal Article"]},
    }


def test_item_matches_expected_issn():
    assert item_matches_journal(_item(), "Science", ["0036-8075", "1095-9203"])
    assert not item_matches_journal(_item(), "Cell", ["0092-8674"])


def test_europe_pmc_entry_maps_to_standard_record():
    entry = europe_pmc_item_to_entry(_item())
    record = entry_to_record(entry, "science", "Science", "2026-07-27T10:00:00+08:00", "Asia/Shanghai")
    assert record["doi"] == "10.1126/science.abc1234"
    assert record["authors_rss"] == ["Jane Doe"]
    assert record["summary_rss"] == "Important abstract."
    assert record["china_team_status"] == "china_led"
    assert record["china_key_authors"] == ["Jane Doe"]


def test_europe_pmc_metadata_contains_identifiers_affiliation_and_china_hint():
    metadata = europe_pmc_metadata(_item())
    assert metadata["pmid"] == "12345678"
    assert metadata["pmcid"] == "PMC123"
    assert metadata["affiliations"] == ["Peking University, Beijing, China"]
    assert metadata["publication_types"] == ["Journal Article"]
    assert metadata["china_team_status"] == "china_led"
    assert metadata["china_institutions"] == ["Peking University, Beijing, China"]
