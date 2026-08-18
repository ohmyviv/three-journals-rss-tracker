from three_journals_tracker.china_team import classify_china_team, is_mainland_china_affiliation


def test_no_affiliation_is_unknown_and_names_are_not_inferred():
    result = classify_china_team(
        author_affiliations=[{"author": "Wei Zhang", "affiliations": []}],
        source="test",
    )
    assert result["china_team_status"] == "unknown"
    assert result["china_institutions"] == []


def test_non_china_affiliations_are_checked_without_positive_signal():
    result = classify_china_team(
        author_affiliations=[
            {"author": "A", "affiliations": ["Massachusetts Institute of Technology, Cambridge, MA, USA"]},
            {"author": "B", "affiliations": ["Stanford University, Stanford, CA, USA"]},
        ],
        source="crossref",
    )
    assert result["china_team_status"] == "no_china_signal"
    assert result["china_team_evidence"] == ["crossref:affiliation_checked"]


def test_mixed_collaboration_is_china_participating():
    result = classify_china_team(
        author_affiliations=[
            {"author": "A", "affiliations": ["Broad Institute, Cambridge, MA, USA"]},
            {"author": "B", "affiliations": ["Fudan University, Shanghai, China"]},
            {"author": "C", "affiliations": ["Harvard Medical School, Boston, MA, USA"]},
        ],
        source="europe_pmc",
    )
    assert result["china_team_status"] == "china_participating"
    assert result["china_key_authors"] == ["B"]


def test_first_and_last_china_affiliations_are_conservatively_china_led():
    result = classify_china_team(
        author_affiliations=[
            {"author": "A", "affiliations": ["Peking University, Beijing, China"]},
            {"author": "B", "affiliations": ["University of Oxford, Oxford, UK"]},
            {"author": "C", "affiliations": ["Tsinghua University, Beijing, China"]},
        ],
        source="crossref",
    )
    assert result["china_team_status"] == "china_led"
    assert "crossref:first_last_author_affiliations" in result["china_team_evidence"]


def test_hong_kong_is_not_folded_into_mainland_hint():
    assert not is_mainland_china_affiliation("The University of Hong Kong, Hong Kong, China")
    result = classify_china_team(
        affiliations=["The University of Hong Kong, Hong Kong, China"],
        source="test",
    )
    assert result["china_team_status"] == "no_china_signal"
