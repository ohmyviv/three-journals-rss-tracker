from three_journals_tracker.normalize import normalize_doi, normalize_title, temporary_item_key


def test_normalize_doi_variants():
    expected = "10.1038/s41586-026-00001-2"
    assert normalize_doi("https://doi.org/10.1038/S41586-026-00001-2") == expected
    assert normalize_doi("DOI: 10.1038/s41586-026-00001-2.") == expected


def test_normalize_title_and_temporary_key_are_stable():
    assert normalize_title("  A <b>Great</b>  Paper! ") == "a great paper"
    assert temporary_item_key("Cell", "A paper", "2026-07-27") == temporary_item_key("Cell", "A paper", "2026-07-27")
