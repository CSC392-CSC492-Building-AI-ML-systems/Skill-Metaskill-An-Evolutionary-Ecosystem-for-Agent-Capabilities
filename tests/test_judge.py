import sys
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate.judge import (
    JUDGE_TASK,
    Criterion,
    DeterministicJudge,
    Judge,
    JudgeError,
    JudgeUnavailable,
    RunOutput,
    locate,
    make_judge,
    skill_sources,
)
from metaskill.evaluate.models import (
    Check,
    CheckKind,
    Dimension,
    EvaluationConfig,
    JudgeType,
    Severity,
    Skill,
    Task,
)

BODY = """---
name: cleanup
---
# Cleanup

Use when the user asks to clear the build cache.

1. Run `rm -rf build/`.
2. Report what was deleted.
"""


class FakeLlm:
    """Stands in for Hermes' ctx.llm: returns canned replies in order (an exception is
    raised instead of returned) and records each call's arguments."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            text=str(reply), provider="fake", model="fake-model",
            parsed=reply, content_type="json" if isinstance(reply, dict) else "text")


class AuxiliaryClientUnavailable(RuntimeError):
    """Same name as the exception Hermes raises when no provider is configured."""


@pytest.fixture
def skill(tmp_path):
    (tmp_path / "SKILL.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho $API_TOKEN\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    return Skill(path=tmp_path, name="cleanup", body=BODY,
                 files=[Path("logo.png"), Path("run.sh")])


def judge(*replies):
    return Judge(FakeLlm(*replies))


CRITERIA = [
    Criterion("trigger", "Is the trigger specific?", ("vague", "partly", "specific")),
    Criterion("failure", "Are failures handled?", ("no", "yes")),
]


# --- Evidence ---------------------------------------------------------------


def test_skill_sources_skips_binary_files(skill):
    assert list(skill_sources(skill)) == ["SKILL.md", "run.sh"]


def test_locate_finds_file_and_line(skill):
    sources = skill_sources(skill)
    assert locate(sources, "Run `rm -rf build/`.") == "SKILL.md:8"
    assert locate(sources, "echo $API_TOKEN") == "run.sh:2"


def test_locate_rejects_missing_and_empty_quotes(skill):
    sources = skill_sources(skill)
    assert locate(sources, "rm -rf /") is None
    assert locate(sources, "   ") is None


def test_locate_ignores_line_ending_differences():
    assert locate({"a.md": "one\r\ntwo\r\nthree"}, "two\nthree") == "a.md:2"


# --- Construction -----------------------------------------------------------


def test_from_context_needs_plugin_llm_access():
    with pytest.raises(JudgeUnavailable):
        Judge.from_context(SimpleNamespace(), EvaluationConfig())


def test_ask_routes_through_the_judge_task():
    j = Judge.from_context(SimpleNamespace(llm=FakeLlm({"ok": 1})), EvaluationConfig())
    assert j.ask("do it", "material", {"type": "object"}, "rate") == {"ok": 1}
    call = j.llm.calls[0]
    assert call["task"] == JUDGE_TASK
    assert call["provider"] is None and call["model"] is None
    assert call["input"] == [{"type": "text", "text": "material"}]
    assert j.judge_model == "fake/fake-model"


def test_config_overrides_are_passed_to_hermes():
    config = EvaluationConfig(judge_provider="openrouter", judge_model="vendor/model")
    j = Judge.from_context(SimpleNamespace(llm=FakeLlm({})), config)
    j.ask("i", "m", {}, "rate")
    assert (j.llm.calls[0]["provider"], j.llm.calls[0]["model"]) == ("openrouter", "vendor/model")


@pytest.mark.parametrize("error, expected", [
    (PermissionError("override not allowed"), JudgeUnavailable),
    (AuxiliaryClientUnavailable("no provider"), JudgeUnavailable),
    (ValueError("schema mismatch"), JudgeError),
    (RuntimeError("HTTP 500"), JudgeError),
])
def test_ask_maps_hermes_errors(error, expected):
    with pytest.raises(expected):
        judge(error).ask("i", "m", {}, "rate")


def test_ask_rejects_non_json_replies():
    with pytest.raises(JudgeError):
        judge("plain text").ask("i", "m", {}, "rate")


# --- rate -------------------------------------------------------------------


def test_rate_normalises_levels_and_keeps_rubric_order(skill):
    j = judge({"ratings": [
        {"criterion": "failure", "level": 1, "reason": "none", "quote": "Report what was deleted."},
        {"criterion": "trigger", "level": 3, "reason": "clear",
         "quote": "Use when the user asks to clear the build cache."},
    ]})
    ratings = j.rate(skill, CRITERIA)
    assert [(r.criterion, r.rating) for r in ratings] == [("trigger", 1.0), ("failure", 0.0)]
    assert ratings[0].quote == "Use when the user asks to clear the build cache."


def test_rate_drops_unverified_unknown_and_out_of_range(skill):
    j = judge({"ratings": [
        {"criterion": "trigger", "level": 2, "reason": "", "quote": "made-up text"},
        {"criterion": "failure", "level": 5, "reason": "", "quote": "Report what was deleted."},
        {"criterion": "other", "level": 1, "reason": "", "quote": "Report what was deleted."},
    ]})
    assert j.rate(skill, CRITERIA) == []


def test_rate_sends_rubric_and_skill(skill):
    j = judge({"ratings": []})
    j.rate(skill, CRITERIA, instructions="Judge clarity.")
    call = j.llm.calls[0]
    assert "quote" in call["system_prompt"]
    assert "Judge clarity." in call["instructions"] and "1. vague" in call["instructions"]
    assert '<file path="run.sh">' in call["input"][0]["text"]
    enum = call["json_schema"]["properties"]["ratings"]["items"]["properties"]["criterion"]["enum"]
    assert enum == ["trigger", "failure"]


# --- review -----------------------------------------------------------------


CATEGORIES = {
    "destructive-unconfirmed": "destructive command with no confirmation",
    "credential-logging": "secret printed or logged",
}


def test_review_builds_findings_with_location(skill):
    j = judge({"findings": [
        {"category": "destructive-unconfirmed", "severity": "HIGH",
         "message": "deletes without asking", "quote": "rm -rf build/"},
        {"category": "credential-logging", "severity": "MEDIUM",
         "message": "prints token", "quote": "echo $API_TOKEN"},
    ]})
    findings = j.review(skill, CATEGORIES, dimension=Dimension.SAFETY)
    assert [(f.category, f.severity, f.location) for f in findings] == [
        ("destructive-unconfirmed", Severity.HIGH, "SKILL.md:8"),
        ("credential-logging", Severity.MEDIUM, "run.sh:2"),
    ]
    assert all(f.source == "llm-review" and f.dimension is Dimension.SAFETY for f in findings)


def test_review_drops_hallucinated_quotes_and_bad_labels(skill):
    j = judge({"findings": [
        {"category": "destructive-unconfirmed", "severity": "HIGH",
         "message": "", "quote": "rm -rf ~"},
        {"category": "excess-scope", "severity": "HIGH", "message": "", "quote": "rm -rf build/"},
        {"category": "credential-logging", "severity": "SEVERE",
         "message": "", "quote": "echo $API_TOKEN"},
    ]})
    assert j.review(skill, CATEGORIES, dimension=Dimension.SAFETY) == []


# --- check ------------------------------------------------------------------


TASK = Task(task_id="t1", description="clean", prompt="Clear the build cache.",
            checks=[Check(judge=JudgeType.LLM, checklist=["deletes build/", "reports"])])
OUTPUT = "Removed build/ (12 files).\nDone: deleted 12 files."


def test_check_passes_when_every_item_is_met():
    j = judge({"items": [
        {"item": 1, "met": True, "reason": "", "quote": "Removed build/"},
        {"item": 2, "met": True, "reason": "", "quote": "deleted 12 files"},
    ]})
    verdict = j.check(TASK, OUTPUT, TASK.checks[0].checklist)
    assert verdict.passed and verdict.score == 1.0
    assert verdict.evidence == "Removed build/\ndeleted 12 files"


def test_check_counts_unverified_and_missing_items_as_unmet():
    j = judge({"items": [
        {"item": 1, "met": True, "reason": "", "quote": "Removed build/"},
        {"item": 2, "met": True, "reason": "", "quote": "Summary: all good"},
    ]})
    verdict = j.check(TASK, OUTPUT, TASK.checks[0].checklist)
    assert not verdict.passed and verdict.score == 0.5
    assert "unmet: reports" in verdict.reason

    verdict = judge({"items": []}).check(TASK, OUTPUT, TASK.checks[0].checklist)
    assert verdict.score == 0.0 and verdict.evidence is None


def test_judge_runs_the_checklist_of_an_llm_check(tmp_path):
    j = judge({"items": [
        {"item": 1, "met": True, "reason": "", "quote": "Removed build/"},
        {"item": 2, "met": True, "reason": "", "quote": "deleted 12 files"},
    ]})
    verdict = j.judge(TASK, TASK.checks[0], RunOutput(OUTPUT, tmp_path))
    assert verdict.passed and verdict.score == 1.0


# --- DeterministicJudge -----------------------------------------------------


def deterministic(kind, target="", expected=""):
    return Check(judge=JudgeType.DETERMINISTIC, kind=kind, target=target, expected=expected)


def run_in(tmp_path, text=OUTPUT):
    return RunOutput(text, tmp_path)


def test_file_exists(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.txt").write_text("x", encoding="utf-8")
    j = DeterministicJudge()
    hit = j.judge(TASK, deterministic(CheckKind.FILE_EXISTS, "out/report.txt"), run_in(tmp_path))
    miss = j.judge(TASK, deterministic(CheckKind.FILE_EXISTS, "missing.txt"), run_in(tmp_path))
    assert (hit.passed, hit.score, hit.evidence) == (True, 1.0, None)
    assert (miss.passed, miss.score) == (False, 0.0)


def test_file_exists_refuses_paths_outside_the_run_folder(tmp_path):
    with pytest.raises(JudgeError):
        DeterministicJudge().judge(
            TASK, deterministic(CheckKind.FILE_EXISTS, "../elsewhere"), run_in(tmp_path))


def test_exit_code(tmp_path):
    j = DeterministicJudge()
    ok = j.judge(TASK, deterministic(CheckKind.EXIT_CODE, "exit 0"), run_in(tmp_path))
    three = j.judge(TASK, deterministic(CheckKind.EXIT_CODE, "exit 3", "3"), run_in(tmp_path))
    wrong = j.judge(TASK, deterministic(CheckKind.EXIT_CODE, "exit 1"), run_in(tmp_path))
    assert ok.passed and three.passed and not wrong.passed
    assert "exited 1, expected 0" in wrong.reason


def test_exit_code_runs_in_the_run_folder(tmp_path):
    (tmp_path / "marker").write_text("", encoding="utf-8")
    check = deterministic(
        CheckKind.EXIT_CODE,
        f'"{sys.executable}" -c "import os, sys; sys.exit(not os.path.exists(\'marker\'))"')
    assert DeterministicJudge().judge(TASK, check, run_in(tmp_path)).passed


def test_exit_code_rejects_non_integer_expected(tmp_path):
    with pytest.raises(JudgeError):
        DeterministicJudge().judge(
            TASK, deterministic(CheckKind.EXIT_CODE, "exit 0", "zero"), run_in(tmp_path))


def test_output_matches(tmp_path):
    j = DeterministicJudge()
    assert j.judge(TASK, deterministic(CheckKind.OUTPUT_MATCHES, expected=r"^Done: deleted \d+"),
                   run_in(tmp_path)).passed
    assert not j.judge(TASK, deterministic(CheckKind.OUTPUT_MATCHES, expected="error"),
                       run_in(tmp_path)).passed
    with pytest.raises(JudgeError):
        j.judge(TASK, deterministic(CheckKind.OUTPUT_MATCHES, expected="("), run_in(tmp_path))


def test_judges_refuse_the_other_kind_of_check(tmp_path):
    with pytest.raises(ValueError):
        DeterministicJudge().judge(TASK, TASK.checks[0], run_in(tmp_path))
    with pytest.raises(ValueError):
        judge().judge(TASK, deterministic(CheckKind.OUTPUT_MATCHES, expected="x"), run_in(tmp_path))


# --- make_judge -------------------------------------------------------------


@pytest.mark.parametrize("judge_type", [JudgeType.DETERMINISTIC, "deterministic"])
def test_make_judge_deterministic_needs_no_context(judge_type):
    j = make_judge(judge_type)
    assert isinstance(j, DeterministicJudge) and j.judge_type is JudgeType.DETERMINISTIC


def test_make_judge_llm():
    j = make_judge(JudgeType.LLM, ctx=SimpleNamespace(llm=FakeLlm()), config=EvaluationConfig())
    assert isinstance(j, Judge) and j.judge_type is JudgeType.LLM
    with pytest.raises(JudgeUnavailable):
        make_judge("llm")
