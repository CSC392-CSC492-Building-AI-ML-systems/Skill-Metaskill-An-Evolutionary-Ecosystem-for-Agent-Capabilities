"""clarity.py on canned judge replies: no model is called, so CI stays offline."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate import evaluate_skill, rank_skills
from metaskill.evaluate.clarity import (
    CRITERIA, INSTRUCTIONS, RUBRIC_VERSION, WEIGHTS, score, score_from_ratings)
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
        if isinstance(reply, Exception):
            raise reply
        # Like Hermes: parsed only when JSON output is asked for; the judge asks for text.
        structured = bool(kwargs.get("json_mode") or kwargs.get("json_schema"))
        return SimpleNamespace(
            text=json.dumps(reply), provider="fake", model="judge-1",
            parsed=reply if structured else None, content_type="json" if structured else "text",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5))


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


def test_rubric_has_eight_criteria_with_five_levels_each():
    assert NAMES == ["purpose_and_trigger", "conciseness", "accessible_language", "readability",
                     "structure_and_flow", "actionability", "verifiability", "failure_handling"]
    assert all(len(c.levels) == 5 for c in CRITERIA)


def test_folded_checks_are_in_their_criteria():
    by_name = {c.name: c for c in CRITERIA}
    assert "description" in by_name["purpose_and_trigger"].levels[-1]
    assert "contradict" in by_name["structure_and_flow"].levels[-1]
    assert "what the result should be" in by_name["verifiability"].question


def test_every_criterion_has_a_positive_weight():
    assert list(WEIGHTS) == NAMES and all(w > 0 for w in WEIGHTS.values())
    # The agent-facing criteria outweigh the ones another dimension also measures.
    assert WEIGHTS["actionability"] == WEIGHTS["purpose_and_trigger"] == max(WEIGHTS.values())
    assert WEIGHTS["conciseness"] == WEIGHTS["readability"] == min(WEIGHTS.values())


# --- score_from_ratings -------------------------------------------------------


TOTAL = sum(WEIGHTS.values())


def test_all_criteria_rated_is_ok_and_the_weighted_mean():
    result = score_from_ratings([rating(n, 1.0 if i else 0.5) for i, n in enumerate(NAMES)])
    assert result.status is Status.OK
    assert result.value == pytest.approx(1 - 0.5 * WEIGHTS["purpose_and_trigger"] / TOTAL)
    assert "weakest: purpose_and_trigger" in result.reason


def test_a_heavier_criterion_moves_the_score_more():
    def without(name):
        return score_from_ratings([rating(n, 0.0 if n == name else 1.0) for n in NAMES]).value

    assert without("actionability") < without("structure_and_flow") < without("conciseness")


def test_a_missing_criterion_counts_as_zero_and_is_partial():
    result = score_from_ratings([rating(n, 1.0) for n in NAMES[1:]])
    assert result.status is Status.PARTIAL
    assert result.value == pytest.approx(1 - WEIGHTS["purpose_and_trigger"] / TOTAL)
    assert "purpose_and_trigger" in result.detail


def test_files_the_judge_did_not_see_make_it_partial_and_are_named():
    result = score_from_ratings([rating(n, 1.0) for n in NAMES],
                                left_out=["SKILL.md (cut after 100 characters)", "big.md"])
    assert result.status is Status.PARTIAL and result.value == 1.0
    assert "SKILL.md (cut after 100 characters), big.md" in result.detail


def test_missing_criteria_and_left_out_files_are_both_named():
    result = score_from_ratings([rating(n, 1.0) for n in NAMES[1:]], left_out=["big.md"])
    assert "purpose_and_trigger" in result.detail and "big.md" in result.detail


def test_left_out_file_names_are_cleaned_and_capped():
    names = ["a\n# Heading\u200b.md"] + [f"f{i}.md" for i in range(9)]
    detail = score_from_ratings([rating(n, 1.0) for n in NAMES], left_out=names).detail
    assert "\n" not in detail and "\u200b" not in detail
    assert "a # Heading.md" in detail and detail.endswith("and 5 more")


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
    assert result.status is Status.PARTIAL
    assert result.value == pytest.approx(1 - WEIGHTS[NAMES[0]] / TOTAL)


def test_a_skill_over_the_budget_is_partial(skill):
    (skill.path / "notes.md").write_text("x" * 500, encoding="utf-8")
    skill.files = [Path("notes.md")]
    config = EvaluationConfig(judge_retries=0, judge_max_chars=len(BODY) + 10)
    result = score(skill, config, llm=FakeLlm(reply({n: 5 for n in NAMES})))
    assert result.status is Status.PARTIAL and "notes.md" in result.detail


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
    assert report.meta.judge_usage == {"input_tokens": 10, "output_tokens": 5, "calls": 1}
    assert report.meta.clarity_rubric_version == RUBRIC_VERSION


def test_the_header_totals_every_skills_clarity_calls(tmp_path):
    # One judge serves the whole call, so its totals are the call's: two skills, two calls.
    folders = []
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(BODY, encoding="utf-8")
        folders.append(folder)
    ctx = SimpleNamespace(llm=FakeLlm(reply({n: 4 for n in NAMES}), reply({n: 2 for n in NAMES})))
    result = rank_skills(folders, output_path=tmp_path / "reports", ctx=ctx)
    meta = Report.from_dict(json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))).meta
    assert meta.judge_usage == {"input_tokens": 20, "output_tokens": 10, "calls": 2}


def test_a_provider_failure_is_named_in_the_status_and_warnings(tmp_path):
    folders = []
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(BODY, encoding="utf-8")
        folders.append(folder)
    ctx = SimpleNamespace(llm=FakeLlm(reply({n: 4 for n in NAMES}), RuntimeError("HTTP 429")))
    result = rank_skills(folders, output_path=tmp_path / "reports", ctx=ctx)
    report = Report.from_dict(json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8")))
    by_name = {s.name: s.dimensions[Dimension.CLARITY] for s in report.skills}
    assert by_name["first"].status is Status.OK
    assert by_name["second"].status is Status.ERROR and "provider" in by_name["second"].detail
    [warning] = result["warnings"]
    assert "provider did not answer for 1 of 2" in warning and "clarity" in warning
    assert "429" not in json.dumps(result)  # the provider's message stays in the log


def test_a_bad_reply_is_not_called_a_provider_failure(tmp_path):
    folder = tmp_path / "tidy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(BODY, encoding="utf-8")
    ctx = SimpleNamespace(llm=FakeLlm("not json", "still not json"))
    result = evaluate_skill(folder, output_path=tmp_path / "reports", ctx=ctx)
    report = Report.from_dict(json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8")))
    status = report.skills[0].dimensions[Dimension.CLARITY]
    assert status.status is Status.ERROR and status.detail == "failed: JudgeError"
    assert result["warnings"] == []
