"""Efficiency dimension (docs/evaluate-efficiency.md).

How cheaply a skill gets a task done, relative to the baseline (no skill) on the same
task. Pure logic over runner results: it runs no agent and calls no model.

`score_from_runs` holds the scoring method (unweighted geometric mean of per-signal
speedup ratios, squashed to [0, 1], success-gated). `score_runs` is what evaluate calls
with a skill's task runs (the entry point of every task-based dimension, evaluate.md 6);
`score` is for a call that ran no tasks, and reports `skipped`.
"""

from __future__ import annotations

import dataclasses
from statistics import geometric_mean, mean
from typing import TYPE_CHECKING, Any, Callable

from .judge import MAX_REASON_CHARS, clean
from .models import (
    Dimension, DimensionScore, EvaluationConfig, Metrics, Skill, Status, TestResult, Variant)

if TYPE_CHECKING:
    from .runner import RunnerResult

# The four portable, skill-attributable cost signals (docs/evaluate-efficiency.md 1).
# Each reads from Metrics. reproducible_input is cache-agnostic. generation is output_tokens:
# reasoning is already inside the completion/output count Hermes reports (normalize_usage),
# so it must not be added again. A signal that returns None was not measured (e.g. tool_calls
# from a usage file, which does not report them) and is left out of the mean, not smoothed as
# a zero. seconds, the cache split and cost are not here.
_SIGNALS: dict[str, Callable[[Metrics], int | None]] = {
    "input": lambda m: m.reproducible_input,
    "generation": lambda m: m.output_tokens,
    "llm_calls": lambda m: m.llm_calls,
    "tool_calls": lambda m: m.tool_calls,
}


def score(skill: Skill, config: EvaluationConfig, llm: Any = None) -> DimensionScore:
    """Efficiency without task runs: skipped. Evaluate calls score_runs when it ran tasks."""
    return DimensionScore(
        Dimension.EFFICIENCY, None, Status.SKIPPED, reason="Not scored",
        detail="efficiency needs task runs; no tasks were given (tasks_path)")


def score_runs(skill: Skill, config: EvaluationConfig, runs: RunnerResult) -> DimensionScore:
    """Efficiency from one skill's task runs, as evaluate passes them.

    A run the runner could not make (runs.errors: the sandbox or the agent's setup failed)
    is left out, as a task without a baseline is, and the detail says how many. If none
    completed there is nothing to compare, and the dimension is an error, not a zero.
    """
    if not runs.tests:
        if runs.errors:
            return DimensionScore(
                Dimension.EFFICIENCY, None, Status.ERROR, reason="Not scored",
                detail=clean(f"no task run completed ({len(runs.errors)} error(s)); "
                             f"first: {runs.errors[0]}", MAX_REASON_CHARS))
        return DimensionScore(Dimension.EFFICIENCY, None, Status.SKIPPED, reason="Not scored",
                              detail="the task file has no runs to score")
    result = score_from_runs([t for t in runs.tests if t.variant is Variant.WITH_SKILL],
                             [t for t in runs.tests if t.variant is Variant.BASELINE])
    if runs.errors:
        note = f"{len(runs.errors)} run(s) the runner could not make were left out"
        result = dataclasses.replace(result, detail=f"{result.detail}; {note}" if result.detail else note)
    return result


def score_from_runs(with_skill: list[TestResult],
                    baseline: list[TestResult]) -> DimensionScore:
    """Efficiency in [0, 1] from paired task runs (docs/evaluate-efficiency.md 2-4).

    A task counts if any of its with-skill runs passed (a cheap failure is not efficient),
    and its cost is averaged over all of that task's with-skill runs. For each counted task
    and signal, forms the smoothed speedup ratio (baseline + 1)/(with_skill + 1); the
    unweighted geometric mean of all such ratios, mapped by G/(1+G), is the score
    (parity -> 0.5, cheaper -> higher).
    """
    # Success-gating by task: a task counts if ANY of its with-skill runs passed; its cost
    # is then averaged over ALL of that task's with-skill runs, so an expensive failure is
    # not hidden by a cheap pass.
    passed_tasks = {r.task_id for r in with_skill if r.passed}
    with_means = _signal_means([r for r in with_skill if r.task_id in passed_tasks])
    base_means = _signal_means(baseline)

    ratios: list[float] = []
    counted = 0
    for task_id, with_sig in with_means.items():
        base_sig = base_means.get(task_id)
        if base_sig is None:  # no baseline for this task: nothing to compare against
            continue
        added = False
        for name in _SIGNALS:
            w, b = with_sig[name], base_sig[name]
            if w is None or b is None:  # a signal not measured on either side is left out
                continue
            # Additive (Laplace) smoothing: defined when either side is zero, and within a
            # hair of b/w for large counts. So a skill that adds calls against a zero
            # baseline is penalised, and one that removes them is credited.
            ratios.append((b + 1) / (w + 1))
            added = True
        if added:
            counted += 1

    total = len(passed_tasks)
    if not ratios:
        return _skipped(f"{total} task(s) passed with the skill but none had a baseline"
                        if total else "no task passed with the skill")

    g = geometric_mean(ratios)
    value = g / (1 + g)
    verdict = "cheaper" if value > 0.5 else "costlier" if value < 0.5 else "even"
    return DimensionScore(
        Dimension.EFFICIENCY, value, Status.OK,
        reason=f"Cost vs. baseline over {counted} task(s): {verdict}",
        detail=f"{counted} of {total} passed task(s) counted")


def _signal_means(results: list[TestResult]) -> dict[str, dict[str, float | None]]:
    """{task_id: {signal: mean over the task's runs}}. Averaging over the n runs keeps one
    noisy run from setting the ratio. A signal that was not measured on any run (all None)
    stays None, so it is left out of the comparison rather than counted as zero."""
    by_task: dict[str, list[Metrics]] = {}
    for r in results:
        by_task.setdefault(r.task_id, []).append(r.metrics)
    out: dict[str, dict[str, float | None]] = {}
    for task_id, metrics in by_task.items():
        sig: dict[str, float | None] = {}
        for name, signal in _SIGNALS.items():
            values = [v for v in (signal(m) for m in metrics) if v is not None]
            sig[name] = mean(values) if values else None
        out[task_id] = sig
    return out


def _skipped(detail: str) -> DimensionScore:
    return DimensionScore(Dimension.EFFICIENCY, None, Status.SKIPPED,
                          reason="Not scored", detail=detail)
