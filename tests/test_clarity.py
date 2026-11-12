"""clarity.py on canned judge replies: no model is called, so CI stays offline."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate import evaluate_skill
from metaskill.evaluate.clarity import CRITERIA, INSTRUCTIONS, score, score_from_ratings
from metaskill.evaluate.judge import Judge, JudgeError, JudgeUnavailable
from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, Report, RubricRating, Skill, Status)

BODY = "# Tidy\n\nUse when the user asks to tidy a folder.\n\n1. List the files first.\n"
QUOTE = "Use when the user asks to tidy a folder."
NAMES = [c.name for c in CRITERIA]


class FakeLlm:
    """Stands in for Hermes' ctx.llm: returns canned replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        return SimpleNamespace(
            text=json.dumps(reply), provider="fake", model="judge-1", parsed=reply,
            content_type="json", usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def reply(levels):
    """A judge reply rating each named criterion at the given level (1 to 5)."""
    return {"ratings": [{"criterion": name, "level": level, "reason": "r", "quote": QUOTE}
                        for name, level in levels.items()]}


def rating(name, value):
    return RubricRating(criterion=name, rating=value, reason="r", quote=QUOTE)


@pytest.fixture
def skill(tmp_path):
    (tmp_path / "SKILL.md").write_text(BODY, encoding="utf-8")
    return Skill(path=tmp_path, name="tidy", body=BODY)


# --- Rubric -----------------------------------------------------------------


def test_rubric_merges_both_rulesets_with_five_levels_each():
    assert NAMES == ["purpose_and_trigger", "conciseness", "accessible_language", "readability",
                     "structure_and_flow", "verifiability", "failure_handling"]
    assert all(len(c.levels) == 5 for c in CRITERIA)


# --- score_from_ratings -------------------------------------------------------


def test_all_criteria_rated_is_ok_and_the_mean():
    result = score_from_ratings([rating(n, 1.0 if i else 0.5) for i, n in enumerate(NAMES)])
    assert result.status is Status.OK
    assert result.value == pytest.approx(6.5 / 7)
    assert "weakest: purpose_and_trigger" in result.reason


def test_a_missing_criterion_counts_as_zero_and_is_partial():
    result = score_from_ratings([rating(n, 1.0) for n in NAMES[1:]])
    assert result.status is Status.PARTIAL
    assert result.value == pytest.approx(6 / 7)
    assert "purpose_and_trigger" in result.detail


def test_no_usable_rating_is_an_error():
    with pytest.raises(JudgeError):
        score_from_ratings([])


# --- score ------------------------------------------------------------------


def test_score_rates_through_the_judge(skill):
    llm = FakeLlm(reply({n: 5 for n in NAMES}))
    result = score(skill, EvaluationConfig(), judge=Judge(llm, retries=0))
    assert result.dimension is Dimension.CLARITY and result.value == 1.0
    assert [r.criterion for r in result.rubric] == NAMES
    call = llm.calls[0]
    assert INSTRUCTIONS.splitlines()[0] in call["instructions"]
    assert "## failure_handling" in call["instructions"]


def test_unverifiable_quotes_lower_the_score(skill):
    answer = reply({n: 5 for n in NAMES})
    answer["ratings"][0]["quote"] = "text that is not in the skill at all"
    result = score(skill, EvaluationConfig(), judge=Judge(FakeLlm(answer), retries=0))
    assert result.status is Status.PARTIAL and result.value == pytest.approx(6 / 7)


def test_builds_a_judge_from_llm_when_none_is_shared(skill):
    result = score(skill, EvaluationConfig(judge_retries=0), llm=FakeLlm(reply({n: 3 for n in NAMES})))
    assert result.value == 0.5


def test_no_llm_and_no_judge_is_unavailable(skill):
    with pytest.raises(JudgeUnavailable):
        score(skill, EvaluationConfig())


# --- Through the plugin's evaluate ---------------------------------------------


def test_evaluate_scores_clarity_and_records_the_judge_model(tmp_path):
    folder = tmp_path / "tidy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(BODY, encoding="utf-8")
    ctx = SimpleNamespace(llm=FakeLlm(reply({n: 4 for n in NAMES})))
    result = evaluate_skill(folder, output_path=tmp_path / "reports", ctx=ctx)
    data = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))
    report = Report.from_dict(data)
    [skill] = report.skills
    assert skill.scores.clarity == 0.75
    assert skill.dimensions[Dimension.CLARITY].status is Status.OK
    assert len(skill.rubric) == len(CRITERIA)
    assert report.meta.judge_model == "fake/judge-1"
