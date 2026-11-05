"""Integrity of the real-skill ground truth (tests/fixtures/ground-truth) and its scorer."""

from types import SimpleNamespace

import pytest

from ground_truth import FLAGGED, Score, case_paths, file_hashes, load, measure, score, skill_dir
from metaskill.generalize import Action, Category, detect_file
from metaskill.generalize.inventory import SourceFile

CASES = {case["skill"]: case for case in map(load, case_paths())}
CATEGORIES = {c.value for c in Category}


def entries(case):
    yield from case["expected"]
    for group in case["retained"]:
        for line in group["lines"]:
            yield {**group, "line": line}


def test_three_or_four_skills_cover_the_required_shapes():
    assert 3 <= len(CASES) <= 4
    files = [set(case["files"]) for case in CASES.values()]
    assert {"SKILL.md"} in files
    assert any(f.endswith((".py", ".sh")) for fs in files for f in fs)


@pytest.mark.parametrize("skill", sorted(CASES))
def test_annotation_covers_the_pinned_fixture(skill):
    # A changed or added file must be re-reviewed before the annotation is trusted.
    assert CASES[skill]["files"] == file_hashes(skill_dir(CASES[skill]))


@pytest.mark.parametrize("skill", sorted(CASES))
def test_entries_point_at_existing_lines(skill):
    case = CASES[skill]
    line_counts = {f: len((skill_dir(case) / f).read_text(encoding="utf-8").splitlines())
                   for f in case["files"]}
    for entry in entries(case):
        assert entry["file"] in line_counts
        assert 1 <= entry["line"] <= line_counts[entry["file"]]
        assert entry.get("category") in CATEGORIES | {None}
        assert "value" not in entry
    for entry in case["expected"]:
        assert entry["action"] in FLAGGED
    locations = [(e["file"], e["line"], e["category"]) for e in case["expected"]]
    assert len(locations) == len(set(locations))
    assert case["review"]["status"] in {"provisional", "reviewed"}


@pytest.mark.parametrize("path", case_paths(), ids=lambda p: p.parent.name)
def test_annotations_store_no_sensitive_values(path):
    findings = detect_file(SourceFile(path.name, path.read_text(encoding="utf-8")))
    leaked = {Category.EMAIL, Category.PHONE, Category.CREDENTIAL}
    assert not [f for f in findings if f.category in leaked]


def finding(file, line, category, action, column=1):
    return SimpleNamespace(file=file, line=line, column=column,
                           category=Category(category), action=Action(action))


CASE = {"expected": [
    {"file": "SKILL.md", "line": 3, "category": "email", "action": "replace"},
    {"file": "run.sh", "line": 7, "category": "credential", "action": "replace"},
]}


def test_score_counts_detections_misses_and_false_replacements():
    result = score(CASE, [
        finding("SKILL.md", 3, "email", "replace"),
        finding("SKILL.md", 9, "email", "replace"),
        finding("SKILL.md", 12, "repository", "review"),
    ])
    assert (result.expected, result.detected, result.action_agreed) == (2, 1, 1)
    assert result.missed == [("run.sh", 7, "credential")]
    assert result.false_replacements == [("SKILL.md", 9, "email")]
    assert result.unexpected_reviews == [("SKILL.md", 12, "repository")]


def test_matching_requires_the_same_category():
    result = score(CASE, [finding("SKILL.md", 3, "file_path", "replace")])
    assert result.detected == 0
    assert result.false_replacements == [("SKILL.md", 3, "file_path")]


def test_matching_is_one_to_one():
    result = score(CASE, [finding("SKILL.md", 3, "email", "review", column=5),
                          finding("SKILL.md", 3, "email", "replace", column=20)])
    assert (result.detected, result.action_agreed) == (1, 1)
    assert result.unexpected_reviews == [("SKILL.md", 3, "email")]


def test_review_counts_as_detected_but_not_as_the_expected_action():
    result = score(CASE, [finding("SKILL.md", 3, "email", "review")])
    assert (result.detected, result.action_agreed) == (1, 0)


def test_keep_findings_are_not_scored():
    result = score(CASE, [finding("run.sh", 7, "environment_variable", "keep")])
    assert result == Score(expected=2, missed=[("SKILL.md", 3, "email"), ("run.sh", 7, "credential")])


def test_measurement_is_deterministic():
    assert measure() == measure()
