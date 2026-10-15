# Evaluation Scoring Design

Status: proposal for team review. Formulas and thresholds marked **[PROPOSED]** are not approved project decisions.

Intended location: `docs/evaluation/scoring.md`.

## Purpose and Authority

This document specifies how observed evidence becomes task scores, six dimension scores, and an overall ranking. It complements [the evaluation design](../evaluate.md), [the safety design](../evaluate-safety.md), and [the golden dataset design](golden_dataset.md).

The `evaluate-design-docs` branch is authoritative for evaluation. The `generalize-design-docs` branch defines transformation behaviour. This draft fills scoring gaps without claiming that its proposals have been adopted.

Existing design choices include:

- Dimension scores use `[0, 1]`; higher is better.
- Skipped and errored dimensions score `null`, not zero.
- Runtime tasks are prepared and reviewed before execution.
- Quality, Robustness, and Efficiency share task runs with and without the skill.
- Tasks run three times per variant by default.
- Quality considers pass rate and lift over baseline.
- Robustness measures repeated-run consistency, including consistent failure.
- Efficiency uses measured tokens, time, and tool calls.
- Safety uses findings and preserves evidence; a critical finding does not remove a skill from ranking.
- Rankings are evaluated fresh; historical reports are not reused as current scores.

All other numeric choices below are proposals unless explicitly attributed to the existing designs.

## Scoring Conventions

Use full precision for calculations and ordering; round to three decimal places for display. Display rounding must not create artificial computational ties.

Let:

- `t` identify a task.
- `r` identify a repeated run.
- `v` be either `with_skill` or `baseline`.
- `s[t,v,r]` be a task score in `[0, 1]`.
- `p[t,v,r]` be its pass indicator, either zero or one.
- `n` be the configured number of runs per task and variant.

Task weights are equal by default. Any alternative weights must be fixed before examining candidate results and recorded in the run configuration. Tasks are not weighted by whether AI helped author them.

Separate three concepts:

| Concept | Meaning |
| --- | --- |
| Score | Performance according to a defined rule. |
| Status | Whether the requested assessment ran successfully. |
| Coverage | How much of the intended evidence was obtained. |

A zero means observed poor performance. A null means no valid dimension score. A perfect score describes performance on assessed evidence, not universal correctness or safety.

**[PROPOSED]** For runtime dimensions, require a complete planned task/run matrix before publishing a dimension score. If execution is incomplete because of the harness or infrastructure, return `error` or `skipped` as appropriate. Do not silently remove difficult or unavailable tasks from the denominator. A later partial-runtime policy would require explicit design and report changes.

## Task-Level Scoring

### Deterministic assertions and semantic checklists

**[PROPOSED]** Split a task into independently assessable criteria. Each criterion has:

- A stable ID and expected property.
- A deterministic assertion or anchored semantic checklist.
- A positive weight, defaulting to one.
- A required flag indicating whether failure prevents the task from passing.

Criterion ratings use `0`, `0.5`, or `1`:

| Rating | Meaning |
| --- | --- |
| 0 | Required property is absent or contradicted. |
| 0.5 | The predefined partial-credit condition is met. |
| 1 | The property is fully satisfied. |

Deterministic Boolean assertions use zero or one. Partial credit must have a task-specific definition before execution; the judge cannot invent it afterwards.

The raw task score is:

```text
raw_score = sum(criterion_weight × criterion_rating) / sum(criterion_weight)
```

**[PROPOSED]** If any required criterion is below one, the final task score is capped at `0.5`. A task passes only when every required criterion equals one and the raw score is at least `0.8`.

This preserves partial-credit evidence while keeping high scores from hiding a missing essential capability. Required criteria should represent essential task correctness or boundaries, not every desirable detail.

### Invalid assessment

A malformed judge response, missing output caused by the harness, or unavailable prerequisite is not a zero-rated criterion. Retry or report an evaluation error. Candidate-caused failure under a valid test, such as producing no required output, is a genuine failure.

**[PROPOSED]** Allow one retry for invalid judge output, retaining both responses. If still invalid, mark the affected dimension as errored and preserve the reason. Retry policy must be recorded.

### Task example

A report-generation task has three equally weighted criteria:

| Criterion | Required | Rating |
| --- | --- | --- |
| Totals are correct | Yes | 1 |
| Required columns are present | Yes | 1 |
| Summary explains an observed anomaly | No | 0.5 |

Raw and final score are `2.5 / 3 = 0.833333...`; the task passes.

If correct totals instead receive zero, the raw score is `1.5 / 3 = 0.5`, the final score is `0.5`, and the task fails. If the other criteria would produce a raw score above `0.5`, the required-failure cap would apply.

## Quality

### Evidence

Use with-skill and baseline pass rates on identical tasks and equal run counts. Retain mean task scores as supporting evidence even though the initial formula below uses pass rates.

Compute the pass rate within each task, then take the mean across tasks:

```text
P_skill = mean_t(mean_r(p[t,with_skill,r]))
P_base  = mean_t(mean_r(p[t,baseline,r]))
lift    = P_skill - P_base
```

Lift ranges from `-1` to `1` and is reported separately.

### Proposed formula

**[PROPOSED]**

```text
Q = P_skill × (0.75 + 0.25 × lift)
```

The multiplier lies between `0.5` and `1`, so the result is in `[0, 1]`. Positive lift increases the score relative to equal baseline performance; negative lift reduces it. A skill that never passes receives zero.

| With-skill pass rate | Baseline pass rate | Lift | Quality |
| --- | --- | --- | --- |
| 1.0 | 0.0 | 1.0 | 1.000 |
| 1.0 | 1.0 | 0.0 | 0.750 |
| 0.8 | 0.4 | 0.4 | 0.680 |
| 0.4 | 0.8 | -0.4 | 0.260 |
| 0.0 | 0.0 | 0.0 | 0.000 |

### Tradeoff for review

This proposal gives a perfectly successful skill only `0.75` when the baseline is also perfect. That deliberately measures added value as well as correctness, but task difficulty strongly affects lift.

An alternative is `Q = P_skill`, with lift used only as a separate signal and tie-breaker. That is easier to interpret but would change the current design's requirement that Quality combines both.

The team must approve the intended meaning before adopting the proposed coefficients. Always show pass rate and lift so the Quality number does not conceal either.

## Robustness

### Evidence and meaning

Robustness measures repeated-run consistency, not success. All-pass and all-fail sequences can both be maximally consistent.

Report both pass/fail agreement and task-score spread. Do not include baseline consistency in the candidate's Robustness score; retain it as context for the experiment.

### Proposed formula

**[PROPOSED]** For each task, calculate pairwise pass/fail agreement and mean absolute score difference across distinct run pairs:

```text
A_t = mean_(i<j)(1 if p[t,skill,i] == p[t,skill,j] else 0)
D_t = mean_(i<j)(abs(s[t,skill,i] - s[t,skill,j]))
R_t = 0.5 × A_t + 0.5 × (1 - D_t)
R   = mean_t(R_t)
```

Both terms range from zero to one. Require at least two valid runs; one run is insufficient to measure consistency and gives `skipped` Robustness with a clear reason.

For three runs with binary scores:

| Outcomes | Agreement | Mean score difference | Robustness |
| --- | --- | --- | --- |
| Pass, pass, pass | 1 | 0 | 1.000 |
| Fail, fail, fail | 1 | 0 | 1.000 |
| Pass, fail, fail | 1/3 | 2/3 | 0.333 |

When all runs pass but receive different partial-credit scores, agreement stays high while score variability lowers Robustness.

**[OPEN]** Agreement and score spread overlap, particularly for binary scores. The proposed equal mixture is explicit and testable, but the team may choose a single consistency statistic or different weights.

## Clarity

### Rubric

Use the five criteria in the evaluation design, weighted equally initially:

1. Trigger specificity.
2. Step ordering.
3. Verifiability of steps.
4. Failure handling.
5. Human followability.

**[PROPOSED]** Rate each criterion on a five-level anchored scale, normalized to `0`, `0.25`, `0.5`, `0.75`, and `1`.

| Criterion | 0 | 0.5 | 1 |
| --- | --- | --- | --- |
| Trigger specificity | Purpose or trigger is absent or misleading. | Main use is identifiable, but important activation boundaries are unclear. | Intended use and relevant boundaries are explicit. |
| Step ordering | Instructions are contradictory or cannot be followed. | Main sequence is understandable but requires resolving dependencies. | Dependencies and execution order are clear. |
| Verifiability | Success cannot be checked from the instructions. | Some inputs or intermediate results can be checked. | Required inputs, meaningful intermediate checks, and completion conditions are observable. |
| Failure handling | Relevant failures are ignored or mishandled. | Common failure guidance exists but leaves meaningful gaps. | Relevant failures and recovery or stopping behaviour are clear. |
| Human followability | A reader cannot determine what to do. | A knowledgeable reader can proceed with some guessing. | The intended reader can follow the workflow with stated prerequisites. |

The `0.25` and `0.75` levels indicate evidence between adjacent anchors and require an explanation of the remaining gap.

```text
C = mean(criterion ratings)
```

Require real supporting quotes. For an omitted requirement, quote the relevant surrounding workflow and explain the omission; do not invent a quote of absent text. Extra verbosity is not itself evidence of clarity.

**[OPEN]** Criterion-specific examples and intermediate anchors need calibration on the representative skill. A failure-handling criterion can receive full credit when the workflow is simple and its applicable stopping conditions are already explicit; it should not demand irrelevant boilerplate.

## Configuration Cleanliness

### Evidence

Consume generalization findings and inspect configuration documentation and usage. Use an LLM only for contextual setup-necessity judgments. Do not build a second general-purpose PII detector.

**[PROPOSED]** Score four equally weighted properties:

| Property | 0 | 0.5 | 1 |
| --- | --- | --- | --- |
| Necessary setup | Clearly unnecessary or obstructive setup dominates. | A useful setup has removable steps or entries. | Each required step and entry has a justified purpose. |
| Documentation | Required configuration is largely undocumented. | Some requirements or examples are missing. | Required fields have clear descriptions and safe defaults or examples where appropriate. |
| Configuration integrity | Required placeholders are missing, unresolved in a personalized output, or substantially unmapped. | Isolated unused entries or mapping defects exist. | Placeholders and declarations agree and are used appropriately for the evaluated stage. |
| Separation of configuration | Known values that should be configurable remain embedded. | Some approved replacements are correct but reviewed residual defects remain. | Expected values are configurable and no annotated improper residuals remain. |

```text
K = mean(property ratings)
```

Judge the appropriate transformation stage. A declared unresolved placeholder is expected in a generalized template; supplied values are expected in a personalized output. Ambiguous findings intentionally retained under the transformation contract are not automatic failures.

If the task needs no external configuration and the absence of setup is verified, relevant properties can receive one with that explanation. Do not award one merely because a detector produced no findings.

**[OPEN]** These anchors require fixture calibration. Scanner `hardcoded_secrets` and `policy_violation` routing remains open; preserve their evidence without applying an unapproved duplicate penalty.

## Efficiency

### Evidence

Measure tokens, seconds, and tool calls during paired task runs. Keep raw measurements in the report. Static instruction size is supporting evidence and has no numeric contribution in this initial proposal.

### Proposed formula

**[PROPOSED]** For each task, include a resource in numeric comparison only when every planned baseline and with-skill repetition passes the task. Report the fraction of planned tasks that qualify. This avoids rewarding cheap failure and avoids selecting only favourable successful repetitions.

For each qualifying task, compare mean cost per resource:

```text
e_resource = baseline_mean / (baseline_mean + skill_mean)
E_t = mean(e_tokens, e_seconds, e_tool_calls)
E   = mean_t(E_t)
```

Equal cost scores `0.5`; half the baseline cost scores approximately `0.667`; twice the cost scores approximately `0.333`. The score approaches one as the skill becomes much cheaper. It is a relative cost score, not a task-success score.

If both means are zero, score that resource `0.5`: neither has a cost advantage. If only the baseline is zero, score zero; if only the skill is zero, score one. Invalid, negative, or missing measurements are errors rather than zero costs.

**[PROPOSED]** Publish an Efficiency score only if at least half the planned tasks qualify; otherwise use `skipped` with the reason that successful paired evidence is insufficient. Record the qualifying task IDs. Across candidates, numerical Efficiency comparison requires the same qualifying task set; otherwise flag the comparison as limited.

This is a proposed exception to requiring all runtime tasks to contribute to every dimension: all runs must still execute, but only fully successful pairs establish useful cost comparisons.

### Tradeoff for review

The rule can leave Efficiency unavailable when a skill provides strong positive lift and the baseline cannot pass. Raw costs remain useful, but they do not establish cost efficiency at equivalent successful outcomes.

**[OPEN]** The team must choose between this conservative policy, a reviewed cost-per-success method, or another task-conditioned measure. The equal resource weights and 50% coverage threshold are provisional.

## Safety

Follow the safety design's worst-finding approach. Duplicate reports of the same underlying issue must not create extra score penalties. Preserve original backend evidence and sources.

The current safety document supplies the following provisional mapping:

| Worst finding | Score |
| --- | --- |
| None | 1.0 |
| INFO or LOW | 0.9 |
| MEDIUM | 0.6 |
| HIGH | 0.3 |
| CRITICAL | 0.0 |

These values remain **[OPEN]** in the authoritative design and are not finalized here.

No findings means no detected findings at the stated tier, not proof of safety. Record requested and actual tier, available backends, scanner version, own-checks version, and judge settings.

A valid fallback can receive a numeric score with `partial` status. If no usable safety assessment is available, return null with the appropriate skipped/error status. The score must not become one merely because no backend ran.

Do not introduce a new critical-finding exclusion rule for ranking: the current evaluation design retains such skills. Whether they may be executed is a separate unresolved runner policy.

**[OPEN]** The safety document's static-only ranking wording and the evaluation design's ranked, flagged fallback policy need reconciliation before finalizing rank recommendations.

## Overall Score and Ranking

### Proposed aggregation

**[PROPOSED]** Start with an equal-weight arithmetic mean:

```text
Overall = (Q + R + C + K + E + S) / 6
```

Equal weights are a reviewable starting point, not a claim that dimensions are interchangeable. Always display the six component scores, pass rate, lift, and dimension statuses.

A mean allows a high score in one dimension to offset a low score in another. In particular, a critical safety finding can coexist with a high overall score. Preserve that visibility; do not quietly add a safety gate that contradicts the current ranking design.

### Missing and partial dimensions

**[PROPOSED]** If any dimension is null, overall is null and the skill appears as unranked with its available evidence. Do not renormalize the remaining dimensions, because candidates could then be ranked on different criteria.

This resolves an existing open question only if approved by the team.

A partial but numeric Safety result remains ranked and flagged under the current evaluation design. Other partial results require a documented scoring rule. Do not treat `partial` as permission to invent a replacement score.

### Ties and recommendations

**[PROPOSED]** Complete the tie-breaking order as:

1. Overall.
2. Lift.
3. Safety.
4. Quality.
5. Robustness.
6. Clarity.
7. Configuration Cleanliness.
8. Efficiency.

Use the same underlying precision throughout. Skills still tied after all numeric criteria share a rank; sort names only for stable presentation, not to imply superiority.

A recommendation should explain task fit and material weaknesses, not simply repeat rank one. Flag critical findings and partial assessments. How strongly such findings restrict recommendations remains open and is separate from retaining a skill in the ranking.

## Worked Examples

### Complete skill score

Suppose a run yields:

- With-skill pass rate `0.8`, baseline pass rate `0.4`, lift `0.4`.
- Quality `0.8 × (0.75 + 0.25 × 0.4) = 0.68`.
- Robustness `0.90`, from repeated task evidence.
- Clarity `0.80`, from anchored ratings.
- Configuration Cleanliness `0.75`.
- Efficiency `0.60`, with sufficient qualifying paired tasks.
- Safety `0.90`, under the provisional LOW/INFO mapping.

Then:

```text
Overall = (0.68 + 0.90 + 0.80 + 0.75 + 0.60 + 0.90) / 6
        = 0.771666...
Displayed overall = 0.772
```

The other dimension values are illustrative outputs, not labels assigned to an actual skill.

### Consistent failure

A skill fails every task on every repetition and receives task scores of zero. Quality is zero; Robustness is one. Efficiency is unavailable under the successful-pair policy. Its overall is therefore null under the proposed missing-dimension rule.

This outcome exposes why Robustness must not be read as correctness.

### Unavailable judge

The clarity judge is unavailable. Clarity has `skipped` status and a null score. Other evidence remains visible; overall is null and the skill is unranked under the proposal. A missing API key is not scored as bad clarity.

### Safety fallback

A deep safety scan falls back to a valid static assessment whose worst finding is HIGH. Safety is `0.3` under the provisional mapping, status is `partial`, and the reason identifies the missing deep layer.

If all other dimensions are numeric, the current evaluation design permits ranked output with a warning. The report must not imply that this assessment is equivalent to a completed deep scan.

## Variability and Fair Comparisons

Three runs are a practical default, not a statistical guarantee. Report counts, task-level pass rates, score spread, and raw resource measurements.

Keep execution variability separate from judge variability. Deterministic scoring over identical recorded evidence must give identical results; fresh model executions need not.

**[PROPOSED]** Treat small observed overall differences as descriptive unless uncertainty has been assessed. Do not infer statistical significance from displayed decimal places.

If additional runs are needed, use a predeclared rule applied consistently to affected candidates and their baselines. Do not add repetitions only until a preferred candidate wins. A future confidence procedure should resample at task level where appropriate and account for paired tasks; its exact method and minimum sample size remain open.

Do not mix historical reports, different task sets, model settings, rubric versions, or safety settings into one fresh ranking. Flag actual backend fallbacks even when requested settings match.

## Validation Before Adoption

Use fixed recorded evidence to test:

- Task-score bounds, required-criterion gates, partial credit, and pass thresholds.
- Quality for zero, positive, and negative lift.
- Robustness for all-pass, all-fail, and mixed sequences.
- Rubric arithmetic and invalid ratings.
- Efficiency for equal, lower, higher, zero, and missing costs.
- Insufficient successful-pair coverage.
- Safety mappings, deduplication, missing backends, and partial status.
- Null propagation, complete tie handling, and stable presentation.
- Exact reproducibility of scoring over identical evidence.

Calibrate judge anchors against independently reviewed golden cases. Real agent and LLM experiments remain separate from routine CI. Choose acceptance thresholds before observing the final validation set.

## Decisions for Team Review

| Decision | Draft proposal |
| --- | --- |
| Task score and pass | Weighted criterion mean; required-failure cap 0.5; pass threshold 0.8 with required criteria fully satisfied. |
| Quality | Pass rate multiplied by 0.75 plus 0.25 times lift. |
| Robustness | Equal mixture of pairwise outcome agreement and inverse pairwise score difference. |
| Clarity | Five existing criteria, equal weights, five anchored levels. |
| Configuration Cleanliness | Four equally weighted, stage-aware properties. |
| Efficiency | Equal resource weights; successful paired tasks; minimum 50% task coverage. |
| Safety | Retain the existing provisional worst-finding mapping pending approval. |
| Overall | Equal arithmetic mean; require all six numeric dimensions. |
| Ties | Complete the existing precedence; share ranks after all numeric ties. |
| Invalid judge output | One bounded retry, then evaluation error. |

Before implementation, approve or revise these choices together, test their consequences on synthetic result tables, and reconcile them with `evaluate.md` and `evaluate-safety.md`. This draft does not make repository changes or settle decisions on behalf of the team.

