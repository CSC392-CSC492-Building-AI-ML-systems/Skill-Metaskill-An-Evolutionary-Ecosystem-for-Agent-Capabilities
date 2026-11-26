"""Quality from a complete paired run matrix, without agent or judge calls.

The numeric formula is a PLACEHOLDER, not a calibrated or agreed scoring rule.
Discuss and replace _provisional_score before treating Quality as finalized.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import (
    Dimension, DimensionScore, EvaluationConfig, Skill, Status, TestResult, Variant,
)

if TYPE_CHECKING:
    from .runner import RunnerResult

FORMULA_VERSION = "provisional-v1"


@dataclass(frozen=True)
class QualityAssessment:
    """Supporting statistics, separate from the unchanged serialized report models."""

    dimension_score: DimensionScore
    with_skill_pass_rate: float | None = None
    baseline_pass_rate: float | None = None
    lift: float | None = None


def _provisional_score(pass_rate: float, lift: float) -> float:
    # TODO: Replace after a team discussion of success versus added value.
    # In particular, perfect skill AND baseline success currently scores 0.75.
    return pass_rate * (0.75 + 0.25 * lift)


def _unscored(status: Status, reason: str, detail: str = "") -> QualityAssessment:
    return QualityAssessment(DimensionScore(
        dimension=Dimension.QUALITY, value=None, status=status,
        reason=reason, detail=detail,
    ))


def _runner_hint(error: str) -> str:
    # Runner entries start with a task/variant/run label. Report fixed messages,
    # never Docker stderr, provider replies, or candidate-controlled exception text.
    _, _, cause = error.partition(": ")
    if cause.startswith("SandboxUnavailable:"):
        if "the sandbox image is not pulled" in cause:
            return "Sandbox image missing; pull the runner's pinned Docker image."
        if "is too old:" in cause:
            return "Hermes is running a Python version too old for the sandbox."
        if "Docker is not installed" in cause:
            return "Hermes could not find Docker; check the gateway's PATH."
        return "Sandbox unavailable; check Docker from the Hermes gateway."
    if cause.startswith("JudgeError: Quality pilot completion unavailable"):
        return "Model completion unavailable or provider/model changed during the pilot."
    return "Runner failed before producing a judged result."


def assess(results: Iterable[TestResult], *, task_ids: Iterable[str],
           runs_per_task: int, runner_errors: Iterable[str] = ()) -> QualityAssessment:
    """Validate the planned evidence and return a score plus pass rates and lift.

    task_ids must come from the planned tasks, never be inferred from observations:
    otherwise a completely absent task could silently vanish from the denominator.
    Caller must ensure all records come from the same skill and evaluation settings.
    """
    if type(runs_per_task) is not int or runs_per_task < 1:
        raise ValueError("runs_per_task must be a positive integer")
    if isinstance(task_ids, (str, bytes)):
        raise ValueError("task_ids must be an iterable of task identifiers")
    planned = tuple(task_ids)
    if (any(not isinstance(t, str) or not t.strip() for t in planned)
            or len(set(planned)) != len(planned)):
        raise ValueError("task_ids must be distinct nonempty strings")
    planned = tuple(sorted(planned))
    records, errors = tuple(results), tuple(runner_errors)
    if errors:
        hints = " ".join(dict.fromkeys(_runner_hint(error) for error in errors))
        return _unscored(Status.ERROR, "Quality unavailable: runner reported errors.",
                         f"{len(errors)} runner error(s). {hints}")
    if not planned:
        if records:
            return _unscored(Status.ERROR, "Recorded runs have no planned tasks.")
        return _unscored(Status.SKIPPED, "Quality skipped: no tasks were configured.",
                         "Quality requires evaluation tasks and a compatible agent")
    if not records:
        return _unscored(Status.SKIPPED, "Quality skipped: no recorded runs.",
                         "Both with-skill and baseline evidence are required.")

    planned_set = set(planned)
    observed = {}
    for record in records:
        if not isinstance(record, TestResult):
            return _unscored(Status.ERROR, "Quality received an invalid run record.")
        if not isinstance(record.task_id, str) or record.task_id not in planned_set:
            return _unscored(Status.ERROR, "Quality received an unplanned task.")
        try:
            variant = Variant(record.variant)
        except (TypeError, ValueError):
            return _unscored(Status.ERROR, "Quality received an unknown run variant.")
        if type(record.run) is not int or not 1 <= record.run <= runs_per_task:
            return _unscored(Status.ERROR, "Quality received an invalid run index.")
        if (type(record.passed) is not bool
                or type(record.score) not in (int, float)
                or not 0 <= record.score <= 1 or not math.isfinite(record.score)):
            return _unscored(Status.ERROR, "Quality received an invalid run verdict.")
        key = (record.task_id, variant, record.run)
        if key in observed:
            return _unscored(Status.ERROR, "Quality received duplicate run identities.")
        observed[key] = record.passed

    expected_count = len(planned) * runs_per_task * 2
    if len(observed) != expected_count:
        return _unscored(
            Status.ERROR, "Quality unavailable: incomplete paired run matrix.",
            f"Expected {expected_count} runs; received {len(observed)} unique runs.",
        )
    # All keys are constrained to the plan, so the count also proves no slot is missing.
    rates = {}
    counts = {}
    for variant in (Variant.WITH_SKILL, Variant.BASELINE):
        counts[variant] = sum(observed[t, variant, r]
                              for t in planned for r in range(1, runs_per_task + 1))
        rates[variant] = sum(
            sum(observed[t, variant, r] for r in range(1, runs_per_task + 1))
            / runs_per_task for t in planned
        ) / len(planned)
    with_skill, baseline = rates[Variant.WITH_SKILL], rates[Variant.BASELINE]
    lift = with_skill - baseline
    denominator = len(planned) * runs_per_task
    mean_scores = {
        variant: math.fsum(record.score for record in records if Variant(record.variant) == variant)
        / denominator
        for variant in (Variant.WITH_SKILL, Variant.BASELINE)
    }
    dimension = DimensionScore(
        dimension=Dimension.QUALITY, value=_provisional_score(with_skill, lift),
        status=Status.OK,
        detail=(
            f"{len(planned)} task(s), {runs_per_task} run(s) per task per condition. "
            f"Mean task scores: with skill {mean_scores[Variant.WITH_SKILL]:.6f}; "
            f"baseline {mean_scores[Variant.BASELINE]:.6f}."
        ),
        reason=(
            f"With skill {counts[Variant.WITH_SKILL]}/{denominator} passed "
            f"(rate {with_skill:.6f}); baseline {counts[Variant.BASELINE]}/{denominator} "
            f"(rate {baseline:.6f}); lift {lift:+.6f}. "
            f"PLACEHOLDER formula {FORMULA_VERSION}: P_skill * (0.75 + 0.25 * lift); "
            "replace after team discussion."
        ),
    )
    return QualityAssessment(dimension, with_skill, baseline, lift)


def score(skill: Skill, config: EvaluationConfig, results: Iterable[TestResult] = (), *,
          task_ids: Iterable[str] = (), runner_errors: Iterable[str] = (),
          llm: object = None) -> DimensionScore:
    """Uniform scoring-module entry point; skill is not re-read or executed.

    Use assess() when the caller also needs unrounded rates and SkillResult.lift.
    There is no skill identifier in TestResult, so selecting this skill's records
    is the caller's responsibility.
    """
    return assess(results, task_ids=task_ids, runs_per_task=config.runs_per_task,
                  runner_errors=runner_errors).dimension_score


def assess_runs(config: EvaluationConfig, runs: RunnerResult) -> QualityAssessment:
    """assess() over one skill's task runs as the runner returns them: their records, the
    planned task ids (so a task that never ran still counts) and the runner's errors."""
    return assess(runs.tests, task_ids=runs.task_ids, runs_per_task=config.runs_per_task,
                  runner_errors=runs.errors)


def score_runs(skill: Skill, config: EvaluationConfig, runs: RunnerResult) -> DimensionScore:
    """Entry point when evaluate ran a task file (task-based dimensions, evaluate.md 6).
    Evaluate copies the same assessment's lift into SkillResult.lift."""
    return assess_runs(config, runs).dimension_score
