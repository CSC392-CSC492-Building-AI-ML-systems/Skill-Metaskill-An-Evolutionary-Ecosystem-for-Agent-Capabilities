"""Structural checks on the draft Clarity golden dataset
(metaskill/evaluate/golden/synthetic/clarity), and the live-report comparison in
metaskill/evaluate/golden/clarity.py.

These do not judge clarity. They keep the annotations honest: every case points at a real
skill, uses the rubric's criteria, and quotes text that actually appears in that skill; and
each control differs from its base only in the way it says it does.
"""

import pytest

from metaskill.evaluate.clarity import CRITERIA, RUBRIC_VERSION
from metaskill.evaluate.golden.clarity import (
    BY_ID, CASES, CLEAR, DATASET, DEGRADED, INJECTED, REWORDED, ROOT, TOLERANCE, compare,
    controls, main, rank_command, summary)

NAMES = [c.name for c in CRITERIA]

# Provisional proxy for conciseness: clear skills stay under this many words.
MAX_WORDS = 1000


def skill_text(case):
    return (ROOT / case["skill_path"]).read_text(encoding="utf-8")


def without_name(case):
    return skill_text(case).replace(f"name: {case['skill']}\n", "", 1)


def code_blocks(text):
    return text.split("```")[1::2]


def test_dataset_uses_the_rubric_in_clarity_py():
    assert list(DATASET["criteria"]) == NAMES
    assert DATASET["rubric_version"] == RUBRIC_VERSION


def test_every_case_has_a_known_kind():
    assert {c["kind"] for c in CASES} == set(DATASET["kinds"])
    assert len(CLEAR) + len(DEGRADED) + len(INJECTED) + len(REWORDED) == len(CASES)


def test_eleven_clear_skills_each_with_one_unclear_variant():
    assert len(CLEAR) == len(DEGRADED) == 11
    assert len({c["case_id"] for c in CASES}) == len({c["skill"] for c in CASES}) == len(CASES)
    assert sorted(c["variant_of"] for c in DEGRADED) == sorted(c["case_id"] for c in CLEAR)


def test_every_criterion_is_degraded_by_some_variant():
    targeted = {t for c in DEGRADED for t in c["targeted_criteria"]}
    assert targeted == set(NAMES)


def test_actionability_has_a_variant_that_isolates_it():
    assert any(c["targeted_criteria"] == ["actionability"] for c in DEGRADED)


def test_every_skill_folder_has_a_case():
    folders = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert folders == {c["skill"] for c in CASES}


@pytest.mark.parametrize("case", CLEAR, ids=lambda c: c["skill"])
def test_clear_case_covers_every_criterion_once(case):
    assert [e["criterion"] for e in case["expectations"]] == NAMES


@pytest.mark.parametrize("case", DEGRADED, ids=lambda c: c["skill"])
def test_unclear_case_annotates_exactly_its_targets(case):
    targets = case["targeted_criteria"]
    assert targets and set(targets) <= set(NAMES)
    assert [e["criterion"] for e in case["expectations"]] == targets
    assert targets == [n for n in NAMES if n in targets]  # rubric order
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


@pytest.mark.parametrize("case", CLEAR + REWORDED, ids=lambda c: c["skill"])
def test_clear_skills_stay_short(case):
    assert len(skill_text(case).split()) <= MAX_WORDS


# --- Controls -----------------------------------------------------------------


def test_there_are_injection_and_rewording_controls():
    assert len(INJECTED) >= 3 and len(REWORDED) >= 3
    assert len({c["technique"] for c in INJECTED}) == len(INJECTED)  # each a different technique


@pytest.mark.parametrize("case", INJECTED, ids=lambda c: c["skill"])
def test_injected_skill_is_its_base_plus_the_injected_text(case):
    base = BY_ID[case["variant_of"]]
    assert base["kind"] == "degraded"
    assert case["skill"] == base["skill"] + "-injected"
    assert case["injected_text"] in skill_text(case)
    assert without_name(case).replace(case["injected_text"], "", 1) == without_name(base)


@pytest.mark.parametrize("case", REWORDED, ids=lambda c: c["skill"])
def test_reworded_skill_keeps_its_base_code_and_sections(case):
    base = BY_ID[case["variant_of"]]
    assert base["kind"] == "clear"
    assert case["skill"] == base["skill"] + "-reworded"
    mine, theirs = skill_text(case), skill_text(base)
    assert code_blocks(mine) == code_blocks(theirs)
    assert mine.count("\n## ") == theirs.count("\n## ")
    assert mine != theirs and case["changes"]


# --- Live-report comparison --------------------------------------------------


def report(scores):
    """A ranked-evaluation.json shape: {skill: (clarity, {criterion: rating})}."""
    return {"meta": {"clarity_rubric_version": RUBRIC_VERSION}, "skills": [
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


def injected(score, base=0.3, twin=0.9):
    return controls(report({"pdf-merge": (twin, {}), "pdf-merge-unclear": (base, {}),
                            "pdf-merge-unclear-injected": (score, {})}))


def test_an_injection_that_does_not_raise_the_score_passes():
    [check] = injected(0.3 + TOLERANCE)
    assert check.kind == "injected" and check.ok
    assert injected(0.1)[0].ok  # lower is fine: the note can read as padding


def test_an_injection_that_raises_the_score_fails():
    assert not injected(0.3 + TOLERANCE + 0.05)[0].ok
    # Within tolerance of its base, but the base was already close to its clear twin.
    assert not injected(0.85, base=0.8, twin=0.85)[0].ok


def test_rewording_passes_within_tolerance_either_way_and_fails_outside():
    def reworded(score):
        [check] = controls(report({"csv-dedupe": (0.9, {}), "csv-dedupe-reworded": (score, {})}))
        return check

    assert reworded(0.9 - TOLERANCE).ok and reworded(0.9 + 0.1).ok
    assert not reworded(0.7).ok
    assert not reworded(None).ok


def test_summary_and_exit_code_cover_pairs_and_controls(tmp_path, capsys):
    data = report({"csv-dedupe": (0.9, {n: 1.0 for n in NAMES}),
                   "csv-dedupe-unclear": (0.3, {n: 0.25 for n in NAMES}),
                   "csv-dedupe-reworded": (0.6, {})})
    text = summary(compare(data), controls(data))
    assert "1/1 pairs ordered" in text and "0/1 controls within bounds" in text
    path = tmp_path / "ranked-evaluation.json"
    path.write_text(__import__("json").dumps(data), encoding="utf-8")
    assert main([str(path)]) == 1
    assert "Warning" not in capsys.readouterr().out


def test_rank_command_lists_every_golden_skill():
    command = rank_command(ROOT)
    assert command.startswith("/metaskill rank skills/")
    assert command.count(", ") == len(CASES) - 1
