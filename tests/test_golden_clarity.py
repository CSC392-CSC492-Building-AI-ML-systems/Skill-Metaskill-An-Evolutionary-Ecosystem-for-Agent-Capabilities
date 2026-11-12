"""Structural checks on the draft Clarity golden dataset (tests/fixtures/golden/clarity).

These do not judge clarity. They keep the annotations honest: every case points at a real
skill, covers all four criteria, and quotes text that actually appears in that skill.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent / "fixtures" / "golden" / "clarity"
DATASET = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
CASES = DATASET["cases"]
CRITERIA = set(DATASET["criteria"])

# Provisional proxy for criterion 1 (conciseness): positive controls stay short.
MAX_WORDS = 600


def skill_text(case):
    return (ROOT / case["skill_path"]).read_text(encoding="utf-8")


def test_ten_cases_with_unique_ids_and_skills():
    assert len(CASES) == 10
    assert len({c["case_id"] for c in CASES}) == 10
    assert len({c["skill"] for c in CASES}) == 10


def test_every_skill_folder_has_a_case():
    folders = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert folders == {c["skill"] for c in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["skill"])
def test_case_covers_every_criterion_once(case):
    criteria = [e["criterion"] for e in case["expectations"]]
    assert sorted(criteria) == sorted(CRITERIA)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["skill"])
def test_quotes_appear_verbatim_in_the_skill(case):
    text = skill_text(case)
    for expectation in case["expectations"]:
        assert expectation["evidence_quotes"], expectation["expectation_id"]
        for quote in expectation["evidence_quotes"]:
            assert quote in text, f"{expectation['expectation_id']}: {quote!r}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["skill"])
def test_frontmatter_name_matches_folder(case):
    assert f"name: {case['skill']}\n" in skill_text(case)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["skill"])
def test_positive_controls_stay_short(case):
    assert len(skill_text(case).split()) <= MAX_WORDS
