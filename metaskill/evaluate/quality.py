"""Quality from a complete paired run matrix, without agent or judge calls.

The numeric formula is a PLACEHOLDER, not a calibrated or agreed scoring rule.
Discuss and replace _provisional_score before treating Quality as finalized.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .models import (
    Dimension, DimensionScore, EvaluationConfig, Skill, Status, TestResult, Variant,
)

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
        return _unscored(Status.ERROR, "Quality unavailable: runner reported errors.",
                         f"{len(errors)} runner error(s); see runner diagnostics and logs.")
    if not planned:
        if records:
            return _unscored(Status.ERROR, "Recorded runs have no planned tasks.")
        return _unscored(Status.SKIPPED, "Quality skipped: no tasks were configured.")
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
    dimension = DimensionScore(
        dimension=Dimension.QUALITY, value=_provisional_score(with_skill, lift),
        status=Status.OK,
        reason=(
            f"With skill {counts[Variant.WITH_SKILL]}/{denominator} passed "
            f"(rate {with_skill:.6f}); baseline {counts[Variant.BASELINE]}/{denominator} "
            f"(rate {baseline:.6f}); lift {lift:+.6f}. "
            f"PLACEHOLDER formula {FORMULA_VERSION}: P_skill * (0.75 + 0.25 * lift); "
            "replace after team discussion."
        ),
    )
    return QualityAssessment(dimension, with_skill, baseline, lift)


def score(skill: Skill, config: EvaluationConfig, results: Iterable[TestResult], *,
          task_ids: Iterable[str], runner_errors: Iterable[str] = ()) -> DimensionScore:
    """Uniform scoring-module entry point; skill is not re-read or executed.

    Use assess() when the caller also needs unrounded rates and SkillResult.lift.
    There is no skill identifier in TestResult, so selecting this skill's records
    is the caller's responsibility.
    """
    return assess(results, task_ids=task_ids, runs_per_task=config.runs_per_task,
                  runner_errors=runner_errors).dimension_score
