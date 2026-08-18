from __future__ import annotations

import re
from typing import Any

from .normalize import clean_text


# v0.1 is deliberately conservative and affiliation-only. Hong Kong, Macao/Macau,
# and Taiwan are not folded into the mainland-China hint; they can be surfaced by a
# future regional layer without changing this contract.
_EXCLUDED_REGIONS = ("hong kong", "macao", "macau", "taiwan")
_UNAMBIGUOUS_MAINLAND_INSTITUTIONS = (
    "chinese academy of sciences",
    "chinese academy of medical sciences",
    "peking university",
    "tsinghua university",
    "fudan university",
    "zhejiang university",
    "shanghai jiao tong university",
    "university of science and technology of china",
    "sun yat-sen university",
    "sichuan university",
    "nanjing university",
    "wuhan university",
    "tongji university",
    "huazhong university of science and technology",
    "southern university of science and technology",
    "westlake university",
    "peking union medical college",
)


def _unique_text(values: list[Any]) -> list[str]:
    cleaned = [clean_text(value) for value in values]
    return list(dict.fromkeys(value for value in cleaned if value))


def is_mainland_china_affiliation(value: Any) -> bool:
    """Return True only from explicit affiliation/institution text signals.

    This intentionally does not infer nationality, ethnicity, or location from a
    person's name.
    """

    text = clean_text(value).casefold()
    if not text:
        return False
    if any(region in text for region in _EXCLUDED_REGIONS):
        return False
    if "people's republic of china" in text or "mainland china" in text:
        return True
    if re.search(r"\bp\.?\s*r\.?\s*china\b", text):
        return True
    if re.search(r"\bchina\b", text):
        return True
    return any(marker in text for marker in _UNAMBIGUOUS_MAINLAND_INSTITUTIONS)


def classify_china_team(
    *,
    author_affiliations: list[dict[str, Any]] | None = None,
    affiliations: list[Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Build a lightweight, non-scoring mainland-China team hint.

    Status semantics:
    - unknown: no usable affiliation metadata was available;
    - no_china_signal: affiliation metadata was checked but no mainland-China signal was found;
    - china_participating: at least one mainland-China affiliation is present;
    - china_led: conservative affiliation heuristic only: an explicitly marked
      corresponding author is mainland-China-affiliated, every listed author has
      affiliation data and is mainland-China-affiliated, or both first and last
      listed authors are mainland-China-affiliated.
    """

    rows: list[dict[str, Any]] = []
    for raw in author_affiliations or []:
        if not isinstance(raw, dict):
            continue
        author = clean_text(raw.get("author") or raw.get("name"))
        row_affiliations = _unique_text(list(raw.get("affiliations") or []))
        rows.append(
            {
                "author": author,
                "affiliations": row_affiliations,
                "corresponding": bool(raw.get("corresponding")),
            }
        )

    flat_affiliations = _unique_text(list(affiliations or []))
    for row in rows:
        flat_affiliations.extend(row["affiliations"])
    flat_affiliations = list(dict.fromkeys(flat_affiliations))

    evidence_prefix = clean_text(source) or "metadata"
    if not flat_affiliations:
        return {
            "china_team_status": "unknown",
            "china_institutions": [],
            "china_key_authors": [],
            "china_team_evidence": [],
        }

    china_affiliations = [value for value in flat_affiliations if is_mainland_china_affiliation(value)]
    china_authors = [
        row["author"]
        for row in rows
        if row["author"] and any(is_mainland_china_affiliation(value) for value in row["affiliations"])
    ]
    if not china_affiliations:
        return {
            "china_team_status": "no_china_signal",
            "china_institutions": [],
            "china_key_authors": [],
            "china_team_evidence": [f"{evidence_prefix}:affiliation_checked"],
        }

    status = "china_participating"
    evidence = f"{evidence_prefix}:author_affiliation"

    corresponding_china = any(
        row["corresponding"]
        and any(is_mainland_china_affiliation(value) for value in row["affiliations"])
        for row in rows
    )
    every_author_mapped_china = bool(rows) and all(
        row["affiliations"]
        and any(is_mainland_china_affiliation(value) for value in row["affiliations"])
        for row in rows
    )
    first_last_china = bool(rows) and all(
        row["affiliations"]
        and any(is_mainland_china_affiliation(value) for value in row["affiliations"])
        for row in (rows[0], rows[-1])
    )

    if corresponding_china:
        status = "china_led"
        evidence = f"{evidence_prefix}:corresponding_author_affiliation"
    elif every_author_mapped_china:
        status = "china_led"
        evidence = f"{evidence_prefix}:all_author_affiliations"
    elif first_last_china:
        status = "china_led"
        evidence = f"{evidence_prefix}:first_last_author_affiliations"

    return {
        "china_team_status": status,
        "china_institutions": list(dict.fromkeys(china_affiliations)),
        "china_key_authors": list(dict.fromkeys(china_authors)),
        "china_team_evidence": [evidence],
    }
