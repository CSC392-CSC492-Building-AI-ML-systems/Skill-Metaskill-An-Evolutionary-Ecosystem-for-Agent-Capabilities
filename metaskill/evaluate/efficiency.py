"""Efficiency dimension (docs/evaluate-efficiency.md).

How cheaply a skill gets a task done, relative to the baseline (no skill) on the same
task. Pure logic over runner results: it runs no agent and calls no model.

`score_from_runs` holds the scoring method (unweighted geometric mean of per-signal
speedup ratios, squashed to [0, 1], success-gated). `score` is the dimension entry point
the evaluator's SCORERS dispatch calls; the runner is not wired into `evaluate` yet (no
task-based dimension runs tasks -- evaluate.md 3.5), so in the live pipeline it reports
`skipped`. The logic is built and tested ahead of that integration, as safety's was.
"""

from __future__ import annotations

from statistics import geometric_mean, mean
from typing import Any, Callable

from .models import (
    Dimension, DimensionScore, EvaluationConfig, Metrics, Skill, Status, TestResult)

# The four portable, skill-attributable cost signals (docs/evaluate-efficiency.md 1).
# Each reads from Metrics; reproducible_input is cache-agnostic, generation is output plus
# reasoning (both billed like output). seconds, the cache split and cost are not here.
_SIGNALS: dict[str, Callable[[Metrics], int]] = {
    "input": lambda m: m.reproducible_input,
    "generation": lambda m: m.output_tokens + m.reasoning_tokens,
    "llm_calls": lambda m: m.llm_calls,
    "tool_calls": lambda m: m.tool_calls,
}


def score(skill: Skill, config: EvaluationConfig, llm: Any = None) -> DimensionScore:
    """Dimension entry point. The runner is not wired into evaluate yet, so there are no
    task runs to score here; this reports skipped until that integration lands. The real
    scoring is `score_from_runs`, which it will call."""
    return DimensionScore(
        Dimension.EFFICIENCY, None, Status.SKIPPED, reason="Not scored",
        detail="efficiency needs task runs; the runner is not wired into evaluate yet "
               "(evaluate.md 3.5)")


def score_from_runs(with_skill: list[TestResult],
                    baseline: list[TestResult]) -> DimensionScore:
    """Efficiency in [0, 1] from paired task runs (docs/evaluate-efficiency.md 2-4).

    Counts only tasks the skill passed (success-gating): a cheap failure is not efficient.
    For each counted task and each signal positive on both sides, forms the speedup ratio
    baseline/with_skill; the unweighted geometric mean of all such ratios, mapped by
    G/(1+G), is the score (parity -> 0.5, cheaper -> higher).
    """
    passed = [r for r in with_skill if r.passed]
    with_means = _signal_means(passed)
    base_means = _signal_means(baseline)

    ratios: list[float] = []
    counted = 0
    for task_id, with_sig in with_means.items():
        base_sig = base_means.get(task_id)
        if base_sig is None:  # no baseline for this task: cannot form a ratio
            continue
        counted += 1
        for name in _SIGNALS:
            w, b = with_sig[name], base_sig[name]
            if w > 0 and b > 0:  # a signal zero on either side forms no ratio
                ratios.append(b / w)

    if counted == 0:
        return _skipped("no task passed with the skill, or no baseline to compare")
    if not ratios:
        return _skipped("no usable cost signal on the passed tasks")

    g = geometric_mean(ratios)
    value = g / (1 + g)
    return DimensionScore(
        Dimension.EFFICIENCY, value, Status.OK,
        reason=f"Cost vs. baseline over {counted} task(s): "
               f"{'cheaper' if value > 0.5 else 'costlier' if value < 0.5 else 'even'}")


def _signal_means(results: list[TestResult]) -> dict[str, dict[str, float]]:
    """{task_id: {signal: mean over the task's runs}}. Averaging over the n runs keeps one
    noisy run from setting the ratio."""
    by_task: dict[str, list[Metrics]] = {}
    for r in results:
        by_task.setdefault(r.task_id, []).append(r.metrics)
    return {
        task_id: {name: mean(signal(m) for m in metrics) for name, signal in _SIGNALS.items()}
        for task_id, metrics in by_task.items()
    }


def _skipped(detail: str) -> DimensionScore:
    return DimensionScore(Dimension.EFFICIENCY, None, Status.SKIPPED,
                          reason="Not scored", detail=detail)
