import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate.judge import Judge, TokenUsage
from metaskill.evaluate.models import (
    Check,
    CheckKind,
    EvaluationConfig,
    JudgeType,
    Skill,
    Task,
    Variant,
)
from metaskill.evaluate.runner import (
    AgentRun,
    report_dir,
    run_skill,
    run_tasks,
    timestamp,
)


class FakeAgent:
    """Stands in for a Hermes session. `act(prompt, skill, workdir)` returns an AgentRun
    (or raises); every call is recorded with the files the agent found in its folder."""

    def __init__(self, act=None):
        self.act = act or (lambda prompt, skill, workdir: AgentRun(output="done"))
        self.calls = []

    def run(self, prompt, skill, workdir):
        self.calls.append(SimpleNamespace(
            skill=skill.name if skill else None, workdir=workdir,
            files=sorted(p.name for p in workdir.iterdir())))
        return self.act(prompt, skill, workdir)


class FakeLlm:
    def __init__(self, *replies):
        self.replies = list(replies)

    def complete_structured(self, **kwargs):
        reply = self.replies.pop(0)
        return SimpleNamespace(provider="fake", model="judge-model", parsed=reply,
                               content_type="json",
                               usage=SimpleNamespace(input_tokens=50, output_tokens=5))


@pytest.fixture
def skill(tmp_path):
    path = tmp_path / "cleanup"
    path.mkdir()
    (path / "SKILL.md").write_text("# Cleanup\n", encoding="utf-8")
    return Skill(path=path, name="cleanup", body="# Cleanup\n")


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path / "reports" / "2026-10-14-T15-30-02"


def config(runs=2, **kwargs):
    return EvaluationConfig(runs_per_task=runs, **kwargs)


def file_check(target="report.txt"):
    return Check(judge=JudgeType.DETERMINISTIC, kind=CheckKind.FILE_EXISTS, target=target)


def output_check(pattern):
    return Check(judge=JudgeType.DETERMINISTIC, kind=CheckKind.OUTPUT_MATCHES, expected=pattern)


def task(task_id="write-report", checks=None, fixtures=()):
    return Task(task_id=task_id, description="Write a report", prompt="Write report.txt",
                checks=checks or [file_check()], fixtures=list(fixtures))


def writes_report_with_skill(prompt, skill, workdir):
    if skill is not None:
        (workdir / "report.txt").write_text("ok", encoding="utf-8")
    return AgentRun(output="wrote it" if skill else "could not", tokens=120, tool_calls=3)


# --- Running ----------------------------------------------------------------


def test_every_task_runs_n_times_per_variant(skill, run_dir):
    agent = FakeAgent()
    result = run_tasks(skill, [task("a"), task("b")], agent, config=config(runs=3),
                       run_dir=run_dir)
    assert [(t.task_id, t.variant, t.run) for t in result.tests] == [
        (task_id, variant, n)
        for task_id in ("a", "b")
        for variant in (Variant.WITH_SKILL, Variant.BASELINE)
        for n in (1, 2, 3)
    ]
    assert [c.skill for c in agent.calls[:6]] == ["cleanup"] * 3 + [None] * 3


def test_runs_are_judged_and_measured(skill, run_dir):
    result = run_tasks(skill, [task()], FakeAgent(writes_report_with_skill),
                       config=config(runs=1), run_dir=run_dir)
    with_skill, baseline = result.tests
    assert (with_skill.passed, with_skill.score) == (True, 1.0)
    assert (baseline.passed, baseline.score) == (False, 0.0)
    assert with_skill.judge is JudgeType.DETERMINISTIC
    assert (with_skill.metrics.tokens, with_skill.metrics.tool_calls) == (120, 3)
    assert with_skill.metrics.seconds >= 0
    assert result.errors == [] and result.judge_model == ""


def test_each_run_gets_a_fresh_folder_with_the_fixtures(skill, run_dir, tmp_path):
    (tmp_path / "data.csv").write_text("a,b\n", encoding="utf-8")
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "x.txt").write_text("x", encoding="utf-8")
    fixtures = [tmp_path / "data.csv", tmp_path / "inputs"]
    agent = FakeAgent(writes_report_with_skill)
    run_tasks(skill, [task(fixtures=fixtures)], agent, config=config(runs=2), run_dir=run_dir)

    assert all(c.files == ["data.csv", "inputs"] for c in agent.calls)  # no report.txt left over
    assert len({c.workdir for c in agent.calls}) == 4
    assert not any(c.workdir.exists() for c in agent.calls)


def test_checks_are_combined(skill, run_dir):
    checks = [file_check(), output_check("wrote"), output_check("never")]
    result = run_tasks(skill, [task(checks=checks)], FakeAgent(writes_report_with_skill),
                       config=config(runs=1), run_dir=run_dir)
    with_skill = result.tests[0]
    assert not with_skill.passed and with_skill.score == pytest.approx(2 / 3)
    assert "report.txt exists" in with_skill.reason and "does not match /never/" in with_skill.reason


def test_agent_failure_is_a_failed_run(skill, run_dir):
    def crash(prompt, skill, workdir):
        raise RuntimeError("session died")

    result = run_tasks(skill, [task()], FakeAgent(crash), config=config(runs=1),
                       run_dir=run_dir)
    assert all(not t.passed and t.score == 0.0 for t in result.tests)
    assert result.tests[0].reason == "agent failed: RuntimeError: session died"


# --- Logs and output --------------------------------------------------------


def test_full_log_is_written_and_output_points_to_it(skill, run_dir):
    def chatty(prompt, skill, workdir):
        return AgentRun(output="x" * 50, transcript="tool: ls")

    result = run_tasks(skill, [task()], FakeAgent(chatty),
                       config=config(runs=1, output_max_chars=10), run_dir=run_dir)
    log = "cleanup/logs/write-report-with_skill-r1.txt"
    assert result.tests[0].output == f"{'x' * 10}... (full log: {log})"
    text = (run_dir / log).read_text(encoding="utf-8")
    assert "x" * 50 in text and "tool: ls" in text and "Write report.txt" in text
    assert (run_dir / "cleanup/logs/write-report-baseline-r1.txt").exists()


def test_timestamp_and_report_dir():
    now = datetime(2026, 10, 14, 15, 30, 2)
    assert timestamp(now) == "2026-10-14-T15-30-02"
    assert report_dir(EvaluationConfig(), now) == Path("evaluation-reports/2026-10-14-T15-30-02")


# --- Judges -----------------------------------------------------------------


LLM_CHECK = Check(judge=JudgeType.LLM, checklist=["says it wrote the report"])


def test_llm_checks_without_a_judge_are_errors_and_not_run(skill, run_dir):
    agent = FakeAgent()
    result = run_tasks(skill, [task(checks=[LLM_CHECK]), task("other")], agent,
                       config=config(runs=1), run_dir=run_dir)
    assert [t.task_id for t in result.tests] == ["other", "other"]
    assert len(result.errors) == 2 and "JudgeUnavailable" in result.errors[0]
    assert len(agent.calls) == 2  # only the deterministic task ran


def test_llm_judge_is_built_once_and_reports_its_model(skill, run_dir):
    reply = {"items": [{"item": 1, "met": True, "reason": "", "quote": "wrote it"}]}
    ctx = SimpleNamespace(llm=FakeLlm(reply, reply))
    result = run_tasks(skill, [task(checks=[LLM_CHECK])], FakeAgent(writes_report_with_skill),
                       config=config(runs=1, judge_retries=0), run_dir=run_dir, ctx=ctx)
    with_skill, baseline = result.tests
    assert with_skill.passed and with_skill.judge is JudgeType.LLM
    assert with_skill.evidence == "wrote it"
    assert not baseline.passed  # "wrote it" is not in the baseline's output
    assert result.judge_model == "fake/judge-model"
    assert result.judge_usage == TokenUsage(100, 10, 2)


def test_a_broken_check_is_an_error_not_a_failed_run(skill, run_dir):
    result = run_tasks(skill, [task(checks=[output_check("(")])], FakeAgent(),
                       config=config(runs=1), run_dir=run_dir)
    assert result.tests == []
    assert result.errors[0].startswith("write-report with_skill r1: JudgeError")


def test_judges_can_be_injected(skill, run_dir):
    class AlwaysPass:
        judge_type = JudgeType.LLM

        def judge(self, task, check, run):
            from metaskill.evaluate.judge import Verdict
            return Verdict(passed=True, score=1.0, reason="fake", evidence=None)

    result = run_tasks(skill, [task(checks=[LLM_CHECK])], FakeAgent(), config=config(runs=1),
                       run_dir=run_dir, judges={JudgeType.LLM: AlwaysPass()})
    assert [t.passed for t in result.tests] == [True, True] and result.errors == []


# --- Loading tasks ------------------------------------------------------------


def test_run_skill_loads_the_task_file(skill, run_dir, tmp_path):
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([task().to_dict()]), encoding="utf-8")
    result = run_skill(skill, tasks_file, FakeAgent(writes_report_with_skill),
                       config=config(runs=1), run_dir=run_dir)
    assert [t.passed for t in result.tests] == [True, False]
