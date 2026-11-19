"""Robustness dimension (docs/evaluate-robustness.md).

Does the skill behave the same way each time it does the same task? Pure logic over the
runner's with-skill results: it runs no agent and calls no model. Consistency is not
correctness: a skill that fails every run in the same way is robust and has poor Quality.

For every pair of with-skill runs of a task, four agreements in [0, 1] are measured:

- outcome: both passed (1); both failed (0.5, up to 1 the more alike their failures, so
  repeating the same bug is consistent); one passed and one failed (0)
- score: 1 - |score_i - score_j|
- output: line similarity of the two replies (a line diff, so code that changes counts)
- method: line similarity of the two transcripts when the caller has them, otherwise
  how alike the two runs' tool-call and model-call counts are

Output and method are compared only between runs with the same outcome. A pass and a
failure are different results however alike their text or call counts, so such a pair
agrees on nothing but its score term, which keeps scoring.md's binary examples exact.

A task's value is the unweighted mean of the four, each averaged over its run pairs, and
Robustness is the mean over tasks. `score_from_runs` holds the method. `score_from_runner`
scores a runner.RunnerResult as it is: the planned tasks and runs per task, so a lost run
is an error rather than a smaller denominator, and the with-skill transcripts for the
method term. `score` is the dimension entry point the evaluator's SCORERS dispatch calls;
given `runner=` it scores that, and without it, as in `evaluate` today, which does not run
tasks yet (evaluate.md 3.2), it reports `skipped`, as efficiency does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from statistics import mean
from typing import TYPE_CHECKING, Any

from .models import (
    Dimension, DimensionScore, EvaluationConfig, Metrics, Skill, Status, TestResult, Variant)

if TYPE_CHECKING:  # runner imports the judge and the sandbox; scoring needs neither
    from .runner import RunnerResult

# The runner appends " (full log: <skill>/logs/<task>-<variant>-r<run>.txt)" to every
# output (runner.md 2.4). The path differs on every run, so it is removed before comparing.
_LOG_SUFFIX = re.compile(r" \(full log: [^()\n]*\)\Z")

# Lines compared per text. Line diffs are quadratic in the worst case, and a transcript
# can be long; the start of a session is where its approach is set.
MAX_LINES = 2000

# Call counts that describe how a run went about the task, when no transcript is given.
# Token counts are left out: they measure volume, which the output comparison covers.
_METHOD_SIGNALS = (lambda m: m.tool_calls, lambda m: m.llm_calls)

Transcripts = Mapping[tuple[str, int], str]  # (task_id, run) -> transcript


@dataclass(frozen=True)
class TaskConsistency:
    """One task's agreements, each the mean over its distinct run pairs, in [0, 1]."""

    task_id: str
    runs: int
    passed: int
    outcome: float
    score: float
    output: float
    method: float
    method_from_transcripts: bool

    @property
    def value(self) -> float:
        return (self.outcome + self.score + self.output + self.method) / 4


def score(skill: Skill, config: EvaluationConfig, llm: Any = None, *,
          runner: RunnerResult | None = None) -> DimensionScore:
    """Dimension entry point. `runner` is the skill's RunnerResult; the skill is not
    re-read. `evaluate` does not run tasks yet, so it passes none, and this reports
    skipped until it does."""
    if runner is None:
        return _unscored(Status.SKIPPED,
                         "robustness needs repeated task runs; evaluate does not run "
                         "tasks yet (evaluate.md 3.2)")
    return score_from_runner(runner, config)


def score_from_runner(result: RunnerResult, config: EvaluationConfig) -> DimensionScore:
    """Robustness of one skill's RunnerResult, with the evidence rules applied in full:
    every planned task must have config.runs_per_task with-skill runs, and the transcripts
    the runner kept decide the method term where every run of a task has one."""
    return score_from_runs(result.tests, runs_per_task=config.runs_per_task,
                           task_ids=result.task_ids or None,
                           transcripts=result.transcripts)


def score_from_runs(results: Iterable[TestResult], *, runs_per_task: int | None = None,
                    task_ids: Iterable[str] | None = None,
                    transcripts: Transcripts | None = None) -> DimensionScore:
    """Robustness in [0, 1] from the runner's results (docs/evaluate-robustness.md 2-4).

    Only with-skill runs are scored; baseline runs, if given, are reported as context.
    runs_per_task, when given (config.runs_per_task), is the number of runs every task
    must have: a task with fewer lost runs, and scoring it anyway would hide that.
    task_ids, when given, are the planned tasks: one with no with-skill run at all lost
    them all, and must not quietly leave the mean, and a run of an unplanned task is not
    this evaluation's. transcripts maps (task_id, run) to that run's transcript, for the
    method agreement.
    """
    records = list(results)
    with_skill = [r for r in records if r.variant == Variant.WITH_SKILL]
    baseline = [r for r in records if r.variant == Variant.BASELINE]
    if not with_skill:
        return _unscored(Status.SKIPPED, "no with-skill task runs")

    by_task, problem = _group(with_skill)
    if problem:
        return _unscored(Status.ERROR, problem)
    if task_ids is not None:
        planned = set(task_ids)
        if unplanned := sorted(set(by_task) - planned):
            return _unscored(Status.ERROR,
                             f"with-skill runs of unplanned task(s): {', '.join(unplanned)}")
        if lost := sorted(planned - set(by_task)):
            return _unscored(Status.ERROR,
                             f"incomplete run matrix: no with-skill runs of planned "
                             f"task(s) {', '.join(lost)}")
    counts = {len(runs) for runs in by_task.values()}
    if runs_per_task is not None and counts != {runs_per_task}:
        return _unscored(Status.ERROR,
                         f"incomplete run matrix: expected {runs_per_task} with-skill "
                         f"run(s) per task, found {_counts(by_task)}")
    if max(counts) < 2:
        return _unscored(Status.SKIPPED,
                         "one run per task cannot measure consistency; at least two needed")
    if len(counts) > 1 or min(counts) < 2:
        return _unscored(Status.ERROR,
                         f"incomplete run matrix: tasks have different numbers of "
                         f"with-skill runs ({_counts(by_task)})")

    tasks = [task_consistency(runs, transcripts) for _, runs in sorted(by_task.items())]
    value = mean(t.value for t in tasks)
    weakest = min(tasks, key=lambda t: (t.value, t.task_id))
    runs = tasks[0].runs
    method_source = ("transcripts" if all(t.method_from_transcripts for t in tasks)
                     else "call counts" if not any(t.method_from_transcripts for t in tasks)
                     else "transcripts where given, otherwise call counts")
    detail = (f"outcome {mean(t.outcome for t in tasks):.2f}, "
              f"score {mean(t.score for t in tasks):.2f}, "
              f"output {mean(t.output for t in tasks):.2f}, "
              f"method {mean(t.method for t in tasks):.2f} (from {method_source})")
    context = _baseline_agreement(baseline)
    if context is not None:
        detail += f"; baseline outcome agreement {context:.2f} (context, not scored)"
    return DimensionScore(
        Dimension.ROBUSTNESS, value, Status.OK,
        reason=(f"{len(tasks)} task(s), {runs} runs each; least consistent: "
                f"{weakest.task_id} ({weakest.value:.2f})"),
        detail=detail)


def task_consistency(runs: list[TestResult],
                     transcripts: Transcripts | None = None) -> TaskConsistency:
    """The agreements of one task's runs (at least two, all with the skill)."""
    if len(runs) < 2:
        raise ValueError("consistency needs at least two runs")
    transcripts = transcripts or {}
    texts = [transcripts.get((r.task_id, r.run)) for r in runs]
    from_transcripts = all(t is not None for t in texts)
    outcome, scores, outputs, methods = [], [], [], []
    for (i, a), (j, b) in combinations(enumerate(runs), 2):
        outcome.append(_outcome_agreement(a, b))
        scores.append(1 - abs(a.score - b.score))
        if a.passed != b.passed:  # different results: their text and method cannot agree
            outputs.append(0.0)
            methods.append(0.0)
            continue
        outputs.append(_similarity(_output(a), _output(b)))
        methods.append(_similarity(texts[i], texts[j]) if from_transcripts
                       else _call_agreement(a.metrics, b.metrics))
    return TaskConsistency(
        task_id=runs[0].task_id, runs=len(runs), passed=sum(r.passed for r in runs),
        outcome=mean(outcome), score=mean(scores), output=mean(outputs),
        method=mean(methods), method_from_transcripts=from_transcripts)


def _outcome_agreement(a: TestResult, b: TestResult) -> float:
    """1 if both passed; 0 if only one did. Two failures agree on the outcome, and agree
    fully only if they failed the same way: the same failed checks, with the same reasons,
    are the same bug repeated, which is consistent behaviour even though it is wrong."""
    if a.passed != b.passed:
        return 0.0
    if a.passed:
        return 1.0
    return 0.5 + 0.5 * SequenceMatcher(
        None, a.reason.split("; "), b.reason.split("; "), autojunk=False).ratio()


def _call_agreement(a: Metrics, b: Metrics) -> float:
    """How alike two runs' call counts are: per signal the smoothed ratio
    (min + 1) / (max + 1), averaged. A signal not measured on either run is left out."""
    ratios = []
    for signal in _METHOD_SIGNALS:
        x, y = signal(a), signal(b)
        if x is None or y is None:
            continue
        ratios.append((min(x, y) + 1) / (max(x, y) + 1))
    return mean(ratios) if ratios else 1.0


def _output(result: TestResult) -> str:
    return _LOG_SUFFIX.sub("", result.output)


def _similarity(a: str | None, b: str | None) -> float:
    """Line similarity in [0, 1] (difflib's ratio over lines). Lines are compared with
    surrounding whitespace removed and blank lines dropped, so re-indentation or an extra
    blank line is not a different result. Two empty texts are identical."""
    return SequenceMatcher(None, _lines(a), _lines(b), autojunk=False).ratio()


def _lines(text: str | None) -> list[str]:
    lines = (line.strip() for line in (text or "").splitlines())
    return [line for line in lines if line][:MAX_LINES]


def _group(results: list[TestResult]) -> tuple[dict[str, list[TestResult]], str]:
    """{task_id: runs in run order}, or a reason the evidence cannot be scored."""
    by_task: dict[str, list[TestResult]] = {}
    seen: set[tuple[str, int]] = set()
    for r in results:
        if (r.task_id, r.run) in seen:
            return {}, f"duplicate run {r.run} of task {r.task_id}"
        seen.add((r.task_id, r.run))
        by_task.setdefault(r.task_id, []).append(r)
    return {t: sorted(runs, key=lambda r: r.run) for t, runs in by_task.items()}, ""


def _baseline_agreement(baseline: list[TestResult]) -> float | None:
    """Plain pass/fail agreement of the baseline runs, for context (scoring.md: baseline
    consistency is retained but not part of the skill's score)."""
    by_task: dict[str, list[bool]] = {}
    for r in baseline:
        by_task.setdefault(r.task_id, []).append(r.passed)
    pairs = [float(a == b) for outcomes in by_task.values()
             for a, b in combinations(outcomes, 2)]
    return mean(pairs) if pairs else None


def _counts(by_task: Mapping[str, list[TestResult]]) -> str:
    return ", ".join(f"{t}: {len(runs)}" for t, runs in sorted(by_task.items()))


def _unscored(status: Status, detail: str) -> DimensionScore:
    return DimensionScore(Dimension.ROBUSTNESS, None, status, reason="Not scored",
                          detail=detail)
