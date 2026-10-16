import json
from pathlib import Path

import pytest

from metaskill.evaluate.judge import (
    Criterion,
    Judge,
    JudgeError,
    JudgeUnavailable,
    locate,
    make_backend,
    skill_sources,
)
from metaskill.evaluate.models import (
    Check,
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


class FakeBackend:
    """Returns canned replies in order and records each request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, system, prompt, schema):
        self.requests.append((system, prompt, schema))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)


@pytest.fixture
def skill(tmp_path):
    (tmp_path / "SKILL.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho $API_TOKEN\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    return Skill(path=tmp_path, name="cleanup", body=BODY,
                 files=[Path("logo.png"), Path("run.sh")])


def judge(*replies):
    return Judge(FakeBackend(*replies), "fake", "fake-model")


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


def test_unknown_provider_is_unavailable():
    with pytest.raises(JudgeUnavailable):
        make_backend("nope", "model")


def test_missing_key_is_unavailable(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(JudgeUnavailable):
        Judge.from_config(EvaluationConfig())


def test_ask_rejects_bad_json():
    with pytest.raises(JudgeError):
        judge("not json").ask("s", "p", {})
    with pytest.raises(JudgeError):
        judge("[1, 2]").ask("s", "p", {})


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
    system, prompt, schema = j.backend.requests[0]
    assert "quote" in system
    assert "Judge clarity." in prompt and "1. vague" in prompt
    assert '<file path="run.sh">' in prompt
    enum = schema["properties"]["ratings"]["items"]["properties"]["criterion"]["enum"]
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
