# Quality: implementation and provisional scoring rule

Status: implemented over recorded evidence; numeric formula is a placeholder pending team discussion. No live agent validation is claimed.

Code: [quality.py](../../metaskill/evaluate/quality.py). Tests: [test_quality.py](../../tests/test_quality.py).
Design authority: [evaluate.md](../evaluate.md), section 3.1. [The scoring draft](../evaluation/scoring.md#quality) provides the current provisional formula.

## Purpose and boundary

Quality measures task success and added value relative to the same agent without the skill. The module is a pure consumer of recorded TestResult objects. It does not read skill contents, execute the agent, call a judge, retry failed runs, compute other dimensions, or rank skills.

Task-level pass/fail is supplied by the existing judge and runner. The module does not replace that decision with a new threshold on TestResult.score. Partial-credit scores remain evidence for other analyses but do not enter this initial pass-rate formula.

## Interface

| Function | Result |
| --- | --- |
| `assess(results, *, task_ids, runs_per_task, runner_errors=())` | QualityAssessment containing a DimensionScore, with-skill pass rate, baseline pass rate, and lift. |
| `score(skill, config, results=(), *, task_ids=(), runner_errors=(), llm=None)` | DimensionScore only, using config.runs_per_task. The skill parameter preserves the common scoring-module shape; it is not read or executed. |
| `assess_runs(config, runs)` | `assess` over a `RunnerResult`: its tests, planned `task_ids` and errors. |
| `score_runs(skill, config, runs)` | What evaluate calls when it ran a task file (task-based dimensions, [evaluate.md](../evaluate.md) 6): `assess_runs(...).dimension_score`. |

The planned task IDs must come from loaded tasks, not the observed results. Otherwise an entirely absent task could disappear from the denominator. Supply only one skill's results from one compatible evaluation and pass the associated RunnerResult.errors.

Example from trusted orchestration code:

```python
from metaskill.evaluate.quality import assess

assessment = assess(
    runner_result.tests,
    task_ids=[task.task_id for task in tasks],
    runs_per_task=config.runs_per_task,
    runner_errors=runner_result.errors,
)
quality_dimension = assessment.dimension_score
# Only when quality_dimension.status is OK:
# assessment.lift is the unrounded value intended for SkillResult.lift.
```

QualityAssessment is an evaluator-side helper, not a new serialized report field. The DimensionScore uses existing value/status/reason/detail fields and round-trips through existing serialization. Its reason records pass counts, rates, lift, and the placeholder formula version. Its detail records task and repetition counts and the mean judge score for each condition.

The standard evaluator skips Quality when no tasks are configured. With a task file it calls `score_runs` with the skill's runs, and copies the same assessment's lift into `SkillResult.lift` (`_lift` in evaluate's `__init__.py`). Both QualityAssessment and SkillResult represent unavailable lift as None.

### Hermes smoke test

Run `/metaskill quality-pilot` in Hermes. It makes six text-only completions
(three with the bundled reference instructions and three without), using the
active Hermes provider and model. It sends only the bundled synthetic task,
inputs, and profile, with the reference instructions included only in the
with-skill runs. The evaluator keeps expected answers local and judges the
returned JSON deterministically. Provider failures or a change of provider
or model during the comparison leave Quality and lift unscored.

This requires Hermes plugin completion access and the runner's Docker setup.
It measures text-only completion behaviour, not a Hermes session with tools.
Reports use the normal evaluation report directory. To opt in from
`evaluate` itself, run:

```text
/metaskill evaluate tests/fixtures/hermes-skills/email/himalaya --quality-pilot
```

Himalaya's pilot uses three draft scenarios: paginated envelope listing,
a folder name containing spaces, and interpretation of a simulated
authentication failure. It makes 18 text-only completions (three per scenario
and condition). It does not execute Himalaya or access a mailbox. The Markdown
report shows per-task pass counts and observed improvements or regressions;
these comparisons do not change the provisional Quality formula. The flag uses
fixed settings and only accepts the bundled Himalaya and ticket-summary
reference fixtures. Without tasks or the pilot flag, task-based dimensions stay
unscored. Arbitrary skills can use `--tasks <file>` with the shared Hermes agent
for tool-using evaluation; the pilot remains restricted to its bundled fixtures.

The pilot supplies a temporary task file, an explicit completion agent, and its
deterministic judge through the shared evaluator. Agent input/output token counts
and LLM calls are recorded on each TestResult. The report header's judge_usage
records judging costs, including Clarity, separately from task-agent usage.

## Evidence validation

A complete comparison requires each planned task to have exactly one record for every run index from 1 through runs_per_task, for both with_skill and baseline.

The module checks:

- Planned IDs are distinct nonempty strings and runs_per_task is a positive integer.
- Each record belongs to a planned task and a recognized variant.
- Run indices are integers in the planned range, with no duplicate identities.
- Pass indicators are actual Booleans and task scores are finite numbers in [0, 1].
- Every planned task/variant/run slot is present.
- No runner errors were supplied.

Invalid plans raise ValueError because the caller must fix its configuration. Malformed or incomplete observed evidence produces a null Quality score with error status. Nonempty runner errors also produce error status, even if record counts happen to look complete. Detailed runner errors stay in runner diagnostics rather than copying candidate-controlled text into score explanations.

No tasks or no records without runner errors produce skipped status with null score and unavailable supporting rates/lift. Records without a task plan produce error status. A valid completed failure remains part of the matrix and counts as a failure, not missing evidence.

The scorer cannot verify model, prompt, tool, permission, or fixture equivalence from TestResult alone: those settings are not all in that model. The caller must establish that both conditions used the same task requirements, configuration, settings, and resources.

## Calculation

For each task, average its pass indicators over the planned repetitions; then average the task rates equally:

```text
P_skill = mean_t(mean_r(passed[t, with_skill, r]))
P_base  = mean_t(mean_r(passed[t, baseline, r]))
lift    = P_skill - P_base
```

Lift is in [-1, 1]. Rates and scores retain calculation precision; formatting in the reason is for display only. One repetition can establish a Quality observation, although it is not sufficient to measure repeatability for Robustness.

### PLACEHOLDER: replace after team discussion

The implementation isolates this temporary rule in _provisional_score and identifies it as provisional-v1:

```text
Q = P_skill * (0.75 + 0.25 * lift)
```

The coefficients are not calibrated or approved as a final definition. The source contains a TODO to replace this calculation after discussion, and every successful score explanation explicitly labels it PLACEHOLDER. Do not interpret implementation as approval.

| With-skill rate | Baseline rate | Lift | Temporary Q |
| --- | --- | --- | --- |
| 1 | 0 | 1 | 1 |
| 1 | 1 | 0 | 0.75 |
| 0 | 0 | 0 | 0 |
| 1/3 | 2/3 | -1/3 | 2/9 |

In particular, perfect success receives only 0.75 when the baseline is also perfect. Decide whether Quality should combine added value and correctness this way, or keep lift separate. Update the implementation, formula identifier, tests, and scoring documentation together after that decision.

## Pilot and validation

[The ticket-summary pilot](../../metaskill/evaluate/golden/synthetic/ticket-summary/README.md) schedules eight input scenarios across five candidate variants and two profiles. Its existing semantic judge produces Boolean pass decisions and all-or-nothing content scores, without exposing expected answers to the agent.

The pilot adapter already exercises the real task loader, runner, log writer, and TestResult shape with inert agent/sandbox doubles. This module's tests consume controlled recorded evidence to verify positive, zero, and negative lift, consistent failure, multiple tasks, record ordering, null/error cases, malformed evidence, and unchanged DimensionScore serialization. No model, network, or Docker execution is needed.

These checks verify arithmetic and evidence handling, not live skill performance, detection accuracy, or independent approval of gold labels. A capable agent may show zero lift on this simple task; do not weaken its baseline to manufacture improvement.

## Remaining integration decisions

- Done in MET-46 (#38): evaluate calls `score_runs` and copies lift into `SkillResult.lift`.
- Agree on shared coverage and retry rules with the Efficiency owner before accepting partial Quality evidence. Scores based on different surviving tasks can be incomparable.
- Review the numeric Quality definition and replace the placeholder after discussion.
- Independently review gold expectations, then run a small controlled live experiment when the Agent adapter is available.


MET-57 uses the shared task runner and its `score_runs` entry points for Quality,
Robustness and Efficiency. It does not supply a separate Quality task table:
MET-49 (#52) owns the shared per-skill task table and baseline comparison labels.
The pilot's JSON report retains every individual run independently of that table.
