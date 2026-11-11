"""Efficiency scoring (docs/evaluate-efficiency.md): unweighted geomean of per-signal
speedup ratios, squashed to [0, 1], success-gated."""

from pathlib import Path

from metaskill.evaluate.efficiency import score, score_from_runs
from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, JudgeType, Metrics, Skill, Status, TestResult, Variant)


def m(input=0, output=0, reasoning=0, cache_read=0, cache_write=0, llm_calls=0, tool_calls=0):
    return Metrics(input_tokens=input, output_tokens=output, reasoning_tokens=reasoning,
                   cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                   llm_calls=llm_calls, tool_calls=tool_calls)


def result(task_id, metrics, *, passed=True, variant=Variant.WITH_SKILL, run=1):
    return TestResult(
        task_id=task_id, description="d", variant=variant, run=run,
        judge=JudgeType.DETERMINISTIC, passed=passed, score=1.0 if passed else 0.0,
        reason="", metrics=metrics, output="")


EVEN = dict(input=100, output=20, llm_calls=2, tool_calls=1)


def test_parity_with_baseline_scores_half():
    withs = [result("a", m(**EVEN))]
    base = [result("a", m(**EVEN), variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value == 0.5


def test_cheaper_than_baseline_scores_above_half():
    withs = [result("a", m(input=50, output=10, llm_calls=1, tool_calls=1))]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=1),
                   variant=Variant.BASELINE)]
    s = score_from_runs(withs, base)
    assert s.status is Status.OK and s.value > 0.5
    assert s.dimension is Dimension.EFFICIENCY


def test_costlier_than_baseline_scores_below_half():
    withs = [result("a", m(input=200, output=40, llm_calls=4, tool_calls=2))]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=1),
                   variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value < 0.5


def test_failed_with_skill_task_earns_no_credit():
    # A cheap run that failed must not be scored as efficient.
    withs = [result("a", m(input=1, output=1, llm_calls=1, tool_calls=1), passed=False)]
    base = [result("a", m(**EVEN), variant=Variant.BASELINE)]
    s = score_from_runs(withs, base)
    assert s.value is None and s.status is Status.SKIPPED


def test_reproducible_input_counts_cache_tokens():
    # Same logical input, different cache state -> same ratio, so parity holds at 0.5.
    withs = [result("a", m(input=20, cache_read=80, output=20, llm_calls=2, tool_calls=1))]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=1),
                   variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value == 0.5


def test_reasoning_tokens_count_as_generation():
    # with-skill generates more once reasoning is included -> costlier -> below half.
    withs = [result("a", m(input=100, output=20, reasoning=100, llm_calls=2, tool_calls=1))]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=1),
                   variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value < 0.5


def test_zero_signal_on_either_side_is_dropped():
    # tool_calls 0 on both forms no ratio; input still scores parity.
    withs = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=0))]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=0),
                   variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value == 0.5


def test_task_without_baseline_is_dropped():
    withs = [result("a", m(**EVEN)), result("b", m(**EVEN))]
    base = [result("a", m(**EVEN), variant=Variant.BASELINE)]  # no baseline for "b"
    s = score_from_runs(withs, base)
    assert s.status is Status.OK  # scored from "a" alone
    assert "1 task" in s.reason


def test_runs_are_averaged_per_task():
    # Two with-skill runs averaging to the baseline cost -> parity.
    withs = [result("a", m(input=50, output=20, llm_calls=2, tool_calls=1), run=1),
             result("a", m(input=150, output=20, llm_calls=2, tool_calls=1), run=2)]
    base = [result("a", m(input=100, output=20, llm_calls=2, tool_calls=1),
                   variant=Variant.BASELINE)]
    assert score_from_runs(withs, base).value == 0.5


def test_no_runs_is_skipped():
    s = score_from_runs([], [])
    assert s.value is None and s.status is Status.SKIPPED


def test_score_entry_point_is_skipped_until_runner_is_wired():
    s = score(Skill(path=Path("x"), name="x", body=""), EvaluationConfig())
    assert s.value is None and s.status is Status.SKIPPED
    assert s.dimension is Dimension.EFFICIENCY
