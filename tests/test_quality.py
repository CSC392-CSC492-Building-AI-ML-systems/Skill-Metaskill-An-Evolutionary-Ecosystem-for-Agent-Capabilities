"""Quality over fixed evidence; no agent, model, Docker, or network calls."""
from dataclasses import replace

import pytest

from metaskill.evaluate.models import (
    Dimension, DimensionScore, EvaluationConfig, JudgeType, Metrics,
    Skill, Status, TestResult, Variant,
)
from metaskill.evaluate.quality import FORMULA_VERSION, assess, score


def matrix(skill_outcomes, baseline_outcomes, task_id="task-a"):
    return [
        TestResult(task_id=task_id, description="Controlled task", variant=variant,
                   run=i, judge=JudgeType.DETERMINISTIC, passed=passed,
                   score=float(passed), reason="Fixed evidence",
                   metrics=Metrics(tokens=0, seconds=0, tool_calls=0), output="")
        for variant, outcomes in ((Variant.WITH_SKILL, skill_outcomes),
                                  (Variant.BASELINE, baseline_outcomes))
        for i, passed in enumerate(outcomes, 1)
    ]


@pytest.mark.parametrize("skill_runs,base_runs,p_skill,p_base,lift,quality", [
    ([True] * 3, [False] * 3, 1, 0, 1, 1),
    ([True] * 3, [True] * 3, 1, 1, 0, .75),
    ([False] * 3, [False] * 3, 0, 0, 0, 0),
    ([False] * 3, [True] * 3, 0, 1, -1, 0),
    ([True, False, False], [True, True, False], 1/3, 2/3, -1/3, 2/9),
    ([True, True, False], [True, False, False], 2/3, 1/3, 1/3, 5/9),
])
def test_provisional_formula_and_supporting_statistics(skill_runs, base_runs, p_skill, p_base, lift, quality):
    result = assess(matrix(skill_runs, base_runs), task_ids=["task-a"], runs_per_task=3)
    assert result.dimension_score.dimension is Dimension.QUALITY
    assert result.dimension_score.status is Status.OK
    assert result.with_skill_pass_rate == pytest.approx(p_skill)
    assert result.baseline_pass_rate == pytest.approx(p_base)
    assert result.lift == pytest.approx(lift)
    assert result.dimension_score.value == pytest.approx(quality)
    assert "PLACEHOLDER" in result.dimension_score.reason
    assert FORMULA_VERSION in result.dimension_score.reason


def test_multiple_tasks_and_input_order_do_not_change_result():
    runs = matrix([True, True], [True, False])
    runs += matrix([True, False], [False, False], "task-b")
    a = assess(runs, task_ids=["task-a", "task-b"], runs_per_task=2)
    b = assess(reversed(runs), task_ids=["task-b", "task-a"], runs_per_task=2)
    assert a == b
    assert a.with_skill_pass_rate == .75
    assert a.baseline_pass_rate == .25
    assert a.lift == .5
    assert a.dimension_score.value == .65625


@pytest.mark.parametrize("change", ["missing-baseline", "missing-whole-task", "missing-one-run",
                                    "duplicate", "unknown-task", "unknown-variant",
                                    "extra-run", "bad-pass", "bad-score", "bad-run", "not-record"])
def test_invalid_evidence_never_becomes_a_numeric_score(change):
    runs = matrix([True, True], [False, True])
    ids = ["task-a"]
    if change == "missing-baseline":
        runs = runs[:2]
    elif change == "missing-whole-task":
        ids.append("task-b")
    elif change == "missing-one-run":
        runs.pop()
    elif change == "duplicate":
        runs.append(runs[0])
    elif change == "unknown-task":
        runs[0] = replace(runs[0], task_id="unexpected")
    elif change == "unknown-variant":
        runs[0] = replace(runs[0], variant="unknown")
    elif change == "extra-run":
        runs[0] = replace(runs[0], run=3)
    elif change == "bad-pass":
        runs[0] = replace(runs[0], passed="false")
    elif change == "bad-score":
        runs[0].score = float("nan")  # defend against mutation after model construction
    elif change == "bad-run":
        runs[0].run = True
    else:
        runs[0] = {}
    result = assess(runs, task_ids=ids, runs_per_task=2)
    assert result.dimension_score.status is Status.ERROR
    assert result.dimension_score.value is None
    assert result.lift is None
    assert result.with_skill_pass_rate is None and result.baseline_pass_rate is None


def test_runner_error_is_not_hidden_even_when_counts_look_complete():
    result = assess(matrix([True], [False]), task_ids=["task-a"], runs_per_task=1,
                    runner_errors=["Docker failure with candidate-controlled text"])
    assert result.dimension_score.status is Status.ERROR
    assert "candidate-controlled" not in result.dimension_score.detail


@pytest.mark.parametrize("ids", [[], ["task-a"]])
def test_no_evidence_is_skipped_without_inventing_zero(ids):
    result = assess([], task_ids=ids, runs_per_task=3)
    assert result.dimension_score.status is Status.SKIPPED
    assert result.dimension_score.value is None and result.lift is None


def test_runner_error_without_results_is_error_not_skip():
    result = assess([], task_ids=["task-a"], runs_per_task=3, runner_errors=["unavailable"])
    assert result.dimension_score.status is Status.ERROR


@pytest.mark.parametrize("runs", [0, -1, True, 1.5])
def test_invalid_planned_repetition_count_is_caller_error(runs):
    with pytest.raises(ValueError):
        assess([], task_ids=["task-a"], runs_per_task=runs)


@pytest.mark.parametrize("ids", [["task-a", "task-a"], [""], [" "], [None], [[]], "task-a"])
def test_invalid_planned_task_ids_are_caller_error(ids):
    with pytest.raises(ValueError):
        assess([], task_ids=ids, runs_per_task=1)


def test_partial_credit_does_not_replace_the_judges_pass_decision():
    runs = matrix([False], [False])
    runs[0] = replace(runs[0], score=.8)
    assert assess(runs, task_ids=["task-a"], runs_per_task=1).dimension_score.value == 0


def test_wrapper_and_existing_dimension_serialization(tmp_path):
    skill = Skill(path=tmp_path, name="unused", body="")
    records = matrix([True], [False])
    dimension = score(skill, EvaluationConfig(runs_per_task=1), records, task_ids=["task-a"])
    assert dimension.value == 1
    assert DimensionScore.from_dict(dimension.to_dict()).to_dict() == dimension.to_dict()
    assert records[0].passed is True  # records are not mutated


def test_one_repetition_is_enough_for_quality():
    result = assess(matrix([True], [True]), task_ids=["task-a"], runs_per_task=1)
    assert result.dimension_score.status is Status.OK


def test_no_planned_tasks_cannot_accept_observed_results():
    result = assess(matrix([True], [True]), task_ids=[], runs_per_task=1)
    assert result.dimension_score.status is Status.ERROR


def test_quality_accepts_actual_pilot_runner_records(tmp_path):
    from metaskill.evaluate.golden.pilot_runner import prepare_pilot, run_prepared
    from metaskill.evaluate.tasks import load_tasks
    from test_golden_pilot_runner import answering_agent
    from test_runner import FakeSandbox

    prepared = prepare_pilot(tmp_path / "staged")
    run = run_prepared(prepared, answering_agent(), config=EvaluationConfig(runs_per_task=3),
                       run_dir=tmp_path / "reports", sandbox=FakeSandbox())
    result = assess(run.tests, task_ids=[t.task_id for t in load_tasks(prepared.tasks_file)],
                    runs_per_task=3, runner_errors=run.errors)
    assert result.dimension_score.status is Status.OK
    assert result.with_skill_pass_rate == result.baseline_pass_rate == 1
    assert result.lift == 0 and result.dimension_score.value == .75
