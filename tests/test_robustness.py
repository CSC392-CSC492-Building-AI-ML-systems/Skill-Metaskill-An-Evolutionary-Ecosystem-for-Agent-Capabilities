"""Robustness scoring (docs/evaluate-robustness.md): agreement of outcomes, scores,
outputs and methods across repeated with-skill runs. Recorded results only; no agent."""

from pathlib import Path

import pytest

from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, JudgeType, Metrics, Skill, Status, TestResult, Variant)
from metaskill.evaluate.robustness import (
    score, score_from_runner, score_from_runs, task_consistency)
from metaskill.evaluate.runner import RunnerResult

CODE = "def total(xs):\n    return sum(xs)\n"
OTHER_CODE = "import functools\n\ntotal = lambda xs: functools.reduce(int.__add__, xs, 0)\n"


def run(n, passed=True, *, task="a", score=None, output=CODE, reason="ok", tools=2,
        calls=3, variant=Variant.WITH_SKILL):
    return TestResult(
        task_id=task, description="d", variant=variant, run=n,
        judge=JudgeType.DETERMINISTIC, passed=passed,
        score=(1.0 if passed else 0.0) if score is None else score, reason=reason,
        metrics=Metrics(tool_calls=tools, llm_calls=calls),
        output=f"{output} (full log: s/logs/{task}-{variant}-r{n}.txt)")


def value(*runs, **kwargs):
    return score_from_runs(list(runs), **kwargs).value


# --- The scoring.md examples ------------------------------------------------------


def test_identical_passing_runs_are_fully_robust():
    result = score_from_runs([run(1), run(2), run(3)])
    assert result.status is Status.OK and result.dimension is Dimension.ROBUSTNESS
    assert result.value == 1.0


def test_identical_failures_are_fully_robust_but_not_correct():
    bug = dict(passed=False, output="total = 0", reason="output differs")
    assert value(run(1, **bug), run(2, **bug), run(3, **bug)) == 1.0


def test_one_pass_in_three_is_not_robust():
    mixed = [run(1), run(2, passed=False, output="error", reason="crashed"),
             run(3, passed=False, output="error", reason="crashed")]
    task = task_consistency(mixed)
    assert task.outcome == pytest.approx(1 / 3)  # only the two failures agree
    assert task.score == pytest.approx(1 / 3)
    # Output and method agree only between the two failures, so the doc's 0.333 holds.
    assert value(*mixed) == pytest.approx(1 / 3)


# --- Criterion 1: output variance -------------------------------------------------


def test_output_line_differences_lower_robustness():
    same = value(run(1), run(2), run(3))
    varied = value(run(1), run(2, output=OTHER_CODE), run(3))
    assert varied < same
    assert task_consistency([run(1), run(2, output=OTHER_CODE)]).output < 0.5


def test_whitespace_and_the_log_path_are_not_output_variance():
    reindented = "def total(xs):\n\n        return sum(xs)   \n"
    assert value(run(1), run(2, output=reindented)) == 1.0  # log paths differ by run too


# --- Criterion 2: passing consistently --------------------------------------------


def test_consistent_passing_beats_alternating():
    assert value(run(1), run(2), run(3)) > value(run(1), run(2, passed=False), run(3))


def test_partial_credit_spread_lowers_the_score_term():
    task = task_consistency([run(1, score=1.0), run(2, score=0.9), run(3, score=0.8)])
    assert task.outcome == 1.0 and task.score == pytest.approx(1 - (0.1 + 0.2 + 0.1) / 3)


# --- Criterion 3: passing, but by very different methods ---------------------------


def test_passing_by_different_methods_is_less_robust():
    steady = [run(1), run(2), run(3)]
    erratic = [run(1, tools=1, calls=1), run(2, tools=12, calls=9, output=OTHER_CODE),
               run(3, tools=5, calls=4)]
    task = task_consistency(erratic)
    assert task.outcome == 1.0 and task.method < 0.5
    assert value(*erratic) < value(*steady)


def test_transcripts_decide_the_method_when_given():
    runs = [run(1), run(2)]  # identical call counts
    alike = {("a", 1): "read input.csv\nwrite out.csv", ("a", 2): "read input.csv\nwrite out.csv"}
    apart = {("a", 1): "read input.csv\nwrite out.csv",
             ("a", 2): "curl http://example.test\npip install pandas\nwrite out.csv"}
    assert task_consistency(runs, alike).method == 1.0
    different = task_consistency(runs, apart)
    assert different.method_from_transcripts and different.method < 1.0
    assert "from transcripts" in score_from_runs(runs, transcripts=alike).detail


def test_unmeasured_tool_calls_are_left_out_of_the_method():
    runs = [run(1, tools=None, calls=3), run(2, tools=None, calls=3)]
    assert task_consistency(runs).method == 1.0


# --- Criterion 4: the same bug every time ------------------------------------------


def test_the_same_bug_each_run_is_more_robust_than_different_bugs():
    same_bug = [run(n, passed=False, output="total = 0", reason="totals wrong")
                for n in (1, 2, 3)]
    different = [run(1, passed=False, output="total = 0", reason="totals wrong"),
                 run(2, passed=False, output="KeyError: team", reason="agent failed: KeyError"),
                 run(3, passed=False, output="", reason="file missing")]
    assert task_consistency(same_bug).outcome == 1.0
    assert 0.5 <= task_consistency(different).outcome < 1.0
    assert value(*same_bug) > value(*different)


def test_any_shared_failure_still_agrees_more_than_pass_versus_fail():
    two_bugs = [run(1, passed=False, reason="x"), run(2, passed=False, reason="y")]
    split = [run(1), run(2, passed=False, reason="y")]
    assert task_consistency(two_bugs).outcome > task_consistency(split).outcome


# --- Aggregation and evidence ------------------------------------------------------


def test_tasks_are_averaged_and_the_weakest_is_named():
    steady = [run(n, task="steady") for n in (1, 2)]
    flaky = [run(1, task="flaky"), run(2, task="flaky", passed=False, output="x", reason="r")]
    result = score_from_runs(steady + flaky)
    expected = (task_consistency(steady).value + task_consistency(flaky).value) / 2
    assert result.value == pytest.approx(expected)
    assert "least consistent: flaky" in result.reason


def test_input_order_does_not_change_the_score():
    runs = [run(1), run(2, output=OTHER_CODE), run(3, passed=False, reason="r"),
            run(1, task="b"), run(2, task="b")]
    assert value(*runs) == value(*reversed(runs))


def test_baseline_is_context_not_score():
    withs = [run(1), run(2)]
    base = [run(1, variant=Variant.BASELINE), run(2, passed=False, variant=Variant.BASELINE)]
    result = score_from_runs(withs + base)
    assert result.value == 1.0
    assert "baseline outcome agreement 0.00" in result.detail


def test_one_run_per_task_is_skipped():
    result = score_from_runs([run(1), run(1, task="b")])
    assert result.value is None and result.status is Status.SKIPPED
    assert "at least two" in result.detail


def test_no_with_skill_runs_is_skipped():
    result = score_from_runs([run(1, variant=Variant.BASELINE)])
    assert result.value is None and result.status is Status.SKIPPED


@pytest.mark.parametrize("runs, kwargs", [
    ([run(1), run(2), run(1, task="b")], {}),                    # a run of b went missing
    ([run(1), run(2)], {"runs_per_task": 3}),                     # fewer than planned
    ([run(1), run(1), run(2)], {}),                               # duplicate identity
])
def test_incomplete_or_inconsistent_evidence_is_an_error_not_a_score(runs, kwargs):
    result = score_from_runs(runs, **kwargs)
    assert result.value is None and result.status is Status.ERROR


def test_a_planned_task_with_no_runs_is_an_error_not_a_smaller_mean():
    runs = [run(1), run(2)]  # task "b" was planned but lost every run
    assert score_from_runs(runs, runs_per_task=2).status is Status.OK  # undetectable without
    result = score_from_runs(runs, runs_per_task=2, task_ids=["a", "b"])
    assert result.value is None and result.status is Status.ERROR
    assert "planned task(s) b" in result.detail


def test_runs_of_an_unplanned_task_are_an_error():
    result = score_from_runs([run(1), run(2), run(1, task="x"), run(2, task="x")],
                             task_ids=["a"])
    assert result.value is None and result.status is Status.ERROR
    assert "unplanned task(s): x" in result.detail


def test_planned_tasks_that_all_ran_score_as_usual():
    runs = [run(1), run(2), run(1, task="b"), run(2, task="b")]
    assert value(*runs, task_ids=["b", "a"], runs_per_task=2) == value(*runs)


def test_scoring_is_reproducible_over_identical_evidence():
    runs = [run(1), run(2, output=OTHER_CODE, tools=7), run(3, passed=False, reason="r")]
    assert score_from_runs(runs) == score_from_runs(list(runs))


def test_entry_point_without_runner_results_is_skipped(tmp_path):
    # evaluate does not run tasks yet, so it calls score() without a RunnerResult.
    skill = Skill(path=Path(tmp_path), name="s", body="# s\n")
    result = score(skill, EvaluationConfig())
    assert result.value is None and result.status is Status.SKIPPED
    assert "task runs" in result.detail


def test_entry_point_scores_a_runner_result_with_its_plan_and_transcripts(tmp_path):
    skill = Skill(path=Path(tmp_path), name="s", body="# s\n")
    apart = {("a", 1): "read input.csv\nwrite out.csv",
             ("a", 2): "curl http://example.test\npip install pandas\nwrite out.csv"}
    result = RunnerResult(tests=[run(1), run(2)], task_ids=["a"], transcripts=apart)
    scored = score(skill, EvaluationConfig(runs_per_task=2), runner=result)
    assert scored.status is Status.OK and scored.value < 1.0  # the transcripts differ
    assert "from transcripts" in scored.detail
    assert score_from_runner(result, EvaluationConfig(runs_per_task=2)) == scored
    # The config's runs_per_task is enforced: three runs were planned, two were made.
    missing = score_from_runner(result, EvaluationConfig(runs_per_task=3))
    assert missing.status is Status.ERROR and "incomplete run matrix" in missing.detail
