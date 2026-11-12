"""Structural checks on the draft Clarity golden dataset (tests/fixtures/golden/clarity),
and the live-report comparison in golden_clarity.py.

These do not judge clarity. They keep the annotations honest: every case points at a real
skill, uses the rubric's criteria, and quotes text that actually appears in that skill.
"""

import pytest

from golden_clarity import BY_ID, CASES, DATASET, HIGH, LOW, ROOT, compare, rank_command
from metaskill.evaluate.clarity import CRITERIA, RUBRIC_VERSION

NAMES = [c.name for c in CRITERIA]

# Provisional proxy for conciseness: clear skills stay under this many words.
MAX_WORDS = 1000


def skill_text(case):
    return (ROOT / case["skill_path"]).read_text(encoding="utf-8")


def test_dataset_uses_the_rubric_in_clarity_py():
    assert list(DATASET["criteria"]) == NAMES
    assert DATASET["rubric_version"] == RUBRIC_VERSION


def test_ten_clear_skills_each_with_one_unclear_variant():
    assert len(HIGH) == len(LOW) == 10
    assert len({c["case_id"] for c in CASES}) == len({c["skill"] for c in CASES}) == 20
    assert sorted(c["variant_of"] for c in LOW) == sorted(c["case_id"] for c in HIGH)


def test_every_skill_folder_has_a_case():
    folders = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert folders == {c["skill"] for c in CASES}


@pytest.mark.parametrize("case", HIGH, ids=lambda c: c["skill"])
def test_clear_case_covers_every_criterion_once(case):
    assert [e["criterion"] for e in case["expectations"]] == NAMES


@pytest.mark.parametrize("case", LOW, ids=lambda c: c["skill"])
def test_unclear_case_annotates_exactly_its_targets(case):
    targets = case["targeted_criteria"]
    assert targets and set(targets) <= set(NAMES)
    assert [e["criterion"] for e in case["expectations"]] == targets
    assert case["skill"] == BY_ID[case["variant_of"]]["skill"] + "-unclear"


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


@pytest.mark.parametrize("case", HIGH, ids=lambda c: c["skill"])
def test_clear_skills_stay_short(case):
    assert len(skill_text(case).split()) <= MAX_WORDS


# --- Live-report comparison --------------------------------------------------


def report(scores):
    """A ranked-evaluation.json shape: {skill: (clarity, {criterion: rating})}."""
    return {"skills": [
        {"name": name, "scores": {"clarity": value},
         "rubric": [{"criterion": c, "rating": r} for c, r in rubric.items()]}
        for name, (value, rubric) in scores.items()]}


def test_compare_passes_when_the_clear_twin_wins_on_its_targets():
    [pair] = compare(report({
        "pdf-merge": (0.9, {n: 1.0 for n in NAMES}),
        "pdf-merge-unclear": (0.2, {n: 0.25 for n in NAMES}),
    }))
    assert pair.ok and set(pair.criteria) == set(BY_ID["clarity-low-006"]["targeted_criteria"])


def test_compare_fails_a_tie_on_a_target_and_a_missing_score():
    tie = compare(report({
        "pdf-merge": (0.9, {n: 1.0 for n in NAMES}),
        "pdf-merge-unclear": (0.5, {n: 1.0 for n in NAMES}),
    }))
    unscored = compare(report({"pdf-merge": (None, {}), "pdf-merge-unclear": (0.1, {})}))
    assert not tie[0].ok and not unscored[0].ok


def test_rank_command_lists_every_golden_skill():
    command = rank_command(ROOT)
    assert command.startswith("/metaskill rank skills/")
    assert command.count(", ") == len(CASES) - 1
