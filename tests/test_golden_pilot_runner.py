"""Real loader/runner/serialization, with inert existing sandbox and agent doubles."""
import json
from pathlib import Path

import pytest

from metaskill.evaluate.golden.pilot_runner import (
    PILOT_ROOT, prepare_pilot, run_prepared,
)
from metaskill.evaluate.golden.ticket_summary import expected_report
from metaskill.evaluate.judge import JudgeError, RunOutput
from metaskill.evaluate.models import EvaluationConfig, TestResult, Variant
from metaskill.evaluate.runner import AgentRun, run_tasks
from metaskill.evaluate.tasks import SAFE_NAME, load_tasks
# Reuse the runner owner's inert doubles; no host command or Docker executes.
from test_runner import FakeAgent, FakeSandbox


def answering_agent(wrong_with_skill=False):
    def act(prompt, skill, folder):
        assert sorted(p.name for p in folder.iterdir()) == ["config.json", "input.json"]
        assert "public contract" in prompt
        config = json.loads((folder / "config.json").read_text())
        tickets = json.loads((folder / "input.json").read_text())
        answer = expected_report(tickets, config["organization"])
        if wrong_with_skill and skill is not None:
            answer["total_minutes"] += 1
        return AgentRun(output=json.dumps(answer), tokens=10, tool_calls=0)
    return FakeAgent(act)


def test_real_runner_judges_six_runs_and_preserves_existing_result_shape(tmp_path):
    prepared = prepare_pilot(tmp_path / "staged")
    tasks = load_tasks(prepared.tasks_file)
    assert len(tasks) == 1 and SAFE_NAME.fullmatch(tasks[0].task_id)
    assert tasks[0].task_id == "ticket-summary-alpha-normal"
    agent, sandbox = answering_agent(), FakeSandbox()
    result = run_prepared(prepared, agent, config=EvaluationConfig(runs_per_task=3),
                          run_dir=tmp_path / "reports", sandbox=sandbox)
    assert not result.errors
    assert len(result.tests) == 6
    assert all(t.passed and t.score == 1 for t in result.tests)
    assert [t.variant for t in result.tests] == [Variant.WITH_SKILL] * 3 + [Variant.BASELINE] * 3
    assert [c.skill for c in agent.calls] == ["reference"] * 3 + [None] * 3
    assert all(w.closed for w in sandbox.opened)
    assert len(list((tmp_path / "reports/reference/logs").glob("*.txt"))) == 6
    for record in result.tests:
        assert TestResult.from_dict(record.to_dict()).to_dict() == record.to_dict()
    staged_files = {p.relative_to(prepared.tasks_file.parent).as_posix()
                    for p in prepared.tasks_file.parent.rglob("*") if p.is_file()}
    assert staged_files == {
        "tasks.json", "fixtures/ticket-summary-alpha-normal/input.json",
        "fixtures/ticket-summary-alpha-normal/config.json",
    }
    assert not sandbox.commands  # verifier never executes candidate or host commands


def test_wrong_totals_are_a_valid_failed_run_not_missing_evidence(tmp_path):
    prepared = prepare_pilot(tmp_path / "staged")
    result = run_prepared(prepared, answering_agent(wrong_with_skill=True),
                          config=EvaluationConfig(runs_per_task=1),
                          run_dir=tmp_path / "reports", sandbox=FakeSandbox())
    assert not result.errors
    assert [(t.passed, t.score) for t in result.tests] == [(False, 0), (True, 1)]


@pytest.mark.parametrize("variant", ["reference", "hardcoded", "undocumented", "unused", "wrong-totals"])
@pytest.mark.parametrize("profile", ["alpha", "beta"])
def test_every_candidate_and_profile_can_use_same_task_and_correct_gold(tmp_path, variant, profile):
    prepared = prepare_pilot(tmp_path / "staged", variant=variant, profile=profile)
    result = run_prepared(prepared, answering_agent(), config=EvaluationConfig(runs_per_task=1),
                          run_dir=tmp_path / "reports", sandbox=FakeSandbox())
    assert not result.errors
    assert all(t.passed for t in result.tests)
    # This controlled fake ignores skill instructions. Passing is not a live score.


def test_without_semantic_adapter_correct_output_fails_closed(tmp_path):
    prepared = prepare_pilot(tmp_path / "staged")
    result = run_tasks(prepared.skill, load_tasks(prepared.tasks_file), answering_agent(),
                       config=EvaluationConfig(runs_per_task=1),
                       run_dir=tmp_path / "reports", sandbox=FakeSandbox())
    assert not result.errors
    assert all(not t.passed for t in result.tests)


def test_all_scenarios_load_with_unique_safe_ids_and_public_fixtures(tmp_path):
    scenarios = tuple(p.stem for p in (PILOT_ROOT / "inputs").glob("*.json"))
    prepared = prepare_pilot(tmp_path / "staged", scenarios=scenarios)
    tasks = load_tasks(prepared.tasks_file)
    assert len(tasks) == 8 and len({t.task_id for t in tasks}) == 8
    assert all(SAFE_NAME.fullmatch(t.task_id) for t in tasks)
    for task in tasks:
        assert {p.name for p in task.fixtures} == {"input.json", "config.json"}
    result = run_prepared(prepared, answering_agent(), config=EvaluationConfig(runs_per_task=1),
                          run_dir=tmp_path / "reports", sandbox=FakeSandbox())
    assert not result.errors
    assert len(result.tests) == 16 and all(t.passed for t in result.tests)


def test_adapter_rejects_unregistered_task_instead_of_guessing_expected_output(tmp_path):
    prepared = prepare_pilot(tmp_path / "staged")
    task = load_tasks(prepared.tasks_file)[0]
    task.task_id = "unknown"
    with pytest.raises(JudgeError, match="unregistered"):
        prepared.judge.judge(task, task.checks[0], RunOutput(text="{}", workdir=tmp_path))


@pytest.mark.parametrize("options", [
    {"variant": "../reference"}, {"profile": "../alpha"},
    {"scenarios": ()}, {"scenarios": ("normal", "normal")},
    {"scenarios": ("missing",)},
])
def test_invalid_selection_writes_nothing(tmp_path, options):
    destination = tmp_path / "staged"
    with pytest.raises(ValueError):
        prepare_pilot(destination, **options)
    assert not destination.exists()


def test_preparation_does_not_overwrite_existing_directory(tmp_path):
    destination = tmp_path / "staged"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        prepare_pilot(destination)
    assert marker.read_text() == "keep"

