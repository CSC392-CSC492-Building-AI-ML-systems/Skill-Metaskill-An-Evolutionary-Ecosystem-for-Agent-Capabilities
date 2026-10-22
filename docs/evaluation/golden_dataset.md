# Golden Dataset Design

Status: draft for review in PR #6 against `main`.

Intended location: `docs/evaluation/golden_dataset.md`.

## Purpose

The golden dataset provides reviewed reference evidence for evaluating Hermes skills across six dimensions: Quality, Robustness, Clarity, Configuration Cleanliness, Efficiency, and Safety. It supports evaluation of individual skills, comparisons between skills that perform sufficiently similar tasks, and validation of the generalization and re-personalization pipeline.

**Gold consists of expected properties, behaviours, and evidence, not unexplained numeric scores.** The evaluator derives scores from observations assessed against those expectations and the relevant scoring rules.

The dataset also tests the evaluator itself: whether deterministic checks detect known facts, judges apply rubrics sensibly, repeated assessments are sufficiently consistent, and aggregation preserves the intended meaning of each dimension.

This document defines dataset responsibilities and proposed curation rules. It does not implement a dataset, change the scoring architecture, or claim that representative skills and fixtures have already been selected.

## Relationship to Existing Designs

The following branches are authoritative for their respective components:

| Design | Authority | Relationship to this document |
| --- | --- | --- |
| [Skill Evaluation](../evaluate.md) | `main` | Dimension definitions, runtime comparisons, scoring range, reports, and ranking behaviour. |
| [Safety Evaluation](../evaluate-safety.md) | `main` | Scanner integration, own checks, safety tiers, findings, and safety validation. |
| [Generalization and Personalization](../design.md) | `main` | Transformation inputs, outputs, placeholders, privacy constraints, and correctness expectations. |

Relative links above are written for this document's intended repository location. The transformation design is linked across branches because it is maintained separately.

The evaluation design already specifies normalized scores in `[0, 1]`, with `null` for skipped or errored dimensions. It also specifies repeated task execution with and without a skill. These are existing design choices, not open questions introduced by this document.

The project plan assigns generalization and personalization to one team of three and evaluation and test cases to another team of three. Evaluation includes pipeline validation as well as skill scoring. Skill breeding remains a stretch goal and does not require component-level breeding labels in the initial dataset.

Proposed dataset-specific conventions in this document require review. Where an existing design marks a decision **[OPEN]**, this document preserves that uncertainty instead of settling it implicitly. A separate scoring document may be added later; it is not a prerequisite assumed to exist.

## Evaluation Scope

### Static evaluation

Static cases inspect `SKILL.md`, metadata, supporting scripts, configuration declarations, and other relevant files without executing the skill. They support clarity judgments, configuration findings, safety findings, and transformation assertions.

Evaluation consumes the generalization component's detection output as a signal. It should not develop a competing PII detector. A fixture-specific assertion against a known synthetic value is a correctness check, not a replacement detection system.

### Runtime evaluation

Runtime cases define tasks, fixtures, configuration, and expected behaviour. The runner executes each task with the skill and without it, repeating runs under controlled settings. The same observations feed Quality, Robustness, and Efficiency.

Normal inputs, malformed data, missing files, missing configuration, and simulated tool failures are useful task categories. Their correct handling contributes to task success. Repeating those tasks measures robustness under the evaluation design's definition.

### Transformation validation

Transformation cases check original, generalized, and re-personalized artifacts. They test configuration completeness, intended replacements, residual findings, preservation of unrelated content, and round-trip consistency. Behavioural execution is a separate harness activity; generalization and personalization themselves do not execute skill code.

A successful fixture or round trip establishes evidence only for the tested inputs and annotated properties. It does not prove complete sanitization, universal behavioural equivalence, or safety.

## Metrics and Required Evidence

### Quality

Quality measures whether the skill performs its intended task and adds value over the agent working without it. The evaluation design combines with-skill pass rate and lift:

```text
lift = pass_rate(with_skill) - pass_rate(baseline)
```

Golden tasks must provide the same success criteria for both variants. Criteria should describe the requested result rather than require use of a particular skill or implementation.

Evidence includes task outcomes, normalized task scores, reasons, and relevant artifacts. Deterministic checks are preferred; semantic outcomes use task-specific checklists.

The exact formula combining pass rate and lift remains to be finalized. Lift can range from `-1` to `1`; it is distinct from normalized dimension scores.

### Robustness

Robustness measures consistency across repeated executions of the same task. The current design uses agreement of pass/fail outcomes and the spread of task scores, with three runs per task by default.

A skill that fails three times can be consistent and therefore robust under this definition while having poor Quality. The dataset must preserve that distinction.

Include cases that validate aggregation over repeated outcome sequences, such as all passes, all failures, and mixed outcomes. Use recorded or synthetic result sequences for deterministic scoring tests. Live executions measure actual observed consistency and should not have fabricated guarantees about their outcome order.

Edge-case handling remains part of the task's expected behaviour; an edge-case failure alone does not establish low robustness unless repeated outcomes are inconsistent.

### Clarity

Clarity uses an LLM judge against anchored rubric criteria from the evaluation design:

- Trigger specificity.
- Step ordering.
- Verifiability of steps.
- Handling of failure cases.
- Whether a human reader could follow the workflow as well as an agent.

Golden annotations identify the relevant text, expected properties, and rationale for an anchored judgment. Each reported rating must quote supporting skill text. Exact rubric anchors still need agreement; unexplained numeric labels are insufficient.

### Configuration Cleanliness

Configuration Cleanliness measures the difficulty and necessity of setup, alongside separation of reusable logic from personal configuration.

Cases should cover:

- Required configuration entries and whether each is necessary.
- Descriptions and defaults or examples where appropriate.
- Documented, used placeholders, which are positive evidence.
- Undocumented placeholders and unused configuration entries.
- Hardcoded personal or machine-specific values, using generalization findings.
- Setup steps that could reasonably be simplified.

A higher number of fields is not automatically worse when the task requires them. A portable relative path, public documentation link, or existing environment-variable reference is not automatically personal information.

The routing of scanner `hardcoded_secrets` and `policy_violation` findings remains open in the authoritative designs. Cases should preserve those findings and their provenance without silently choosing a scoring destination.

### Efficiency

Efficiency is measured during task execution relative to the baseline without the skill. Primary observations are tokens consumed, wall-clock time, and tool calls. Static instruction and bundle size are minor secondary signals.

Golden cases define comparable tasks and measurement conditions; they should not invent a universal token target for unrelated skills. Record model settings, tokenizer or token accounting method, loaded context policy, and timing boundaries.

Cost reductions must be interpreted alongside task success. A candidate that stops immediately and fails is not evidence of useful efficiency. The scoring formula must specify how failed tasks and zero or missing baseline measurements are handled.

### Safety

Safety evaluates harmful instructions using the external scanner, own checks, and optional LLM review described in the safety design. It is not a new general-purpose security scanner.

The dataset should cover the project's own categories:

| Category | Expected concern |
| --- | --- |
| `destructive-unconfirmed` | Destructive operations without an appropriate confirmation step. |
| `credential-logging` | Instructions to print or log credentials at runtime. |
| `untrusted-source` | Unpinned installs or downloads from unknown sources. |
| `excess-scope` | Permissions or side effects beyond the stated task; assessed in the deep review. |

Include reviewed benign controls and selected malicious-category examples for scanner validation. Public origin alone does not make a skill a benign control.

Annotate expected locations, categories, rationale, and severity where reviewed. Assess detection and false positives by category and tier; do not require every backend to return identical wording or every tier to detect every concern.

Safety findings use the categories in section 2 of [the safety design](../evaluate-safety.md), so gold labels can be compared directly with what the evaluator reports. The kinds of evidence map onto them as follows:

| Evidence | Category |
| --- | --- |
| Unsafe or overly broad tool permissions | `excess-scope`; Cisco's `unauthorized_tool_use` |
| Destructive operations without appropriate safeguards | `destructive-unconfirmed` |
| Secrets printed or logged at run time | `credential-logging`; secrets sent to a third party are Cisco's `data_exfiltration` |
| Unnecessary execution of external commands, or installs from untrusted sources | `untrusted-source`, or `excess-scope` when outside the skill's purpose |
| Instructions that encourage behaviour outside the intended scope of the skill | `excess-scope` |
| Malicious content: prompt injection, hidden instructions, obfuscation, exfiltration | Cisco's categories (safety design, section 2.2) |

Secrets or personal information embedded in the skill's own files are not a safety finding: they belong to Configuration Cleanliness (safety design, section 2). Failure to validate user-controlled paths or configuration has no safety category yet. **[OPEN]** Whether to add one.


The current safety design uses the worst finding to determine the score, with numeric mappings marked as placeholders. Critical findings lower the safety score and do not remove the skill from ranking. Whether flagged skills may be executed by the runner remains open.

A scan with no findings is not proof of safety.

## Dataset Composition

### Synthetic skills and controlled variants

Start by creating a small, self-contained representative skill, since a project reference skill and expected transformation have not yet been selected.

Its task should have observable outputs, synthetic configuration, and minimal external dependencies. Create a reviewed reference version and variants with targeted changes: vague instructions, unnecessary setup, a hardcoded value, unsafe instructions, or redundant context.

Annotate which properties a mutation changes. Do not assume it affects only one dimension or that a textual mutation guarantees a particular live runtime score.

Synthetic values may be stored in fixtures when needed to verify exact replacement. They must be clearly fictitious and must not be functioning credentials or real personal data.

### Real Hermes skills

Real skills test whether conclusions transfer beyond simplified fixtures. The transformation design plans selection of three or four real skills under MET-8; coordinate with that effort rather than assume the examples already exist.

Record source, revision, reuse permissions, sanitization, dependencies, and annotation limitations. For real-skill detection ground truth, follow the transformation design's `expected.json` convention of file, line, and category without storing secret values.

### Coverage

Across the collection, cover all six dimensions, positive and negative controls, static and runtime evidence, and transformation stages. Individual cases may cover only a subset.

Keep development cases separate from held-out validation where practical. Related variants and near-duplicates should remain in the same partition. Exact counts and partition sizes are proposed implementation decisions, not established project requirements.

## Golden Case Structure / Schema

The case schema is not yet frozen. It must distinguish **expected evidence stored in the dataset** from **observed results written by the evaluator**.

| Concept | Required information |
| --- | --- |
| Identity | Stable case ID, revision, description, and source type. |
| Skill reference | Source revision or content hash, family, and variant relationship. |
| Stage | Original, generalized, re-personalized, or round-trip. |
| Inputs | Task prompt, fixtures, configuration, and starting state. |
| Preconditions | Tools, dependencies, environment, execution permissions, and service requirements. |
| Expectations | Independently assessable required properties and forbidden behaviour. |
| Assessment method | Deterministic check, task checklist, anchored rubric, or expected finding. |
| Dimension relationship | Which metric uses the evidence, or which pipeline invariant is tested. |
| Evidence references | Relevant files, locations, expected artifacts, and supporting rationale. |
| Review | Author, reviewer, status, and disagreement resolution. |
| Limitations | Applicability, excluded behaviour, and known uncertainty. |

Paths should resolve relative to the fixture bundle. Pin source material and inputs so the case can be reproduced. Split compound expectations when separate outcomes matter.

### Compatibility with evaluation models

The implemented [evaluation models](../../metaskill/evaluate/models.py) and [evaluation report design](../evaluate.md#21-report-format-open) define observed runtime results. Gold annotations describe expectations and are a separate input contract.

Its `Task` and `Check` objects already represent task IDs, prompts, fixtures, deterministic checks, and LLM checklists. Report objects distinguish task results, clarity ratings, findings, and dimension statuses.

Golden records should reference existing task IDs and fixture paths rather than duplicate report objects. An expected finding is an assertion against a reported `Finding`, not a new runtime `Finding` type.

| Golden input or expectation | Existing runtime model | Integration rule |
| --- | --- | --- |
| Task definition and assertions | `Task`, `Check` | Use existing fields and check kinds; richer assertions need a separately reviewed extension. |
| Task outcome | `TestResult` | Keep `passed`, `score`, `reason`, `evidence`, `metrics`, and `output`; no additional per-result dimension field. |
| Clarity expectation | `RubricRating` | Match criterion, normalized rating expectations, and verified quotes. |
| Safety/configuration expectation | `Finding` | Match the relevant dimension, category, location, and reviewed severity constraint. |
| Assessment availability | `DimensionScore`, `DimensionStatus` | Preserve `ok`, `partial`, `skipped`, and `error`; do not add a not-applicable status in this PR. |
| Overall evidence | `Report`, `ReportMeta`, `SkillResult`, `Scores` | Preserve the documented JSON report shape. |

Case revision, review status, source hashes, and expected-finding constraints belong to golden inputs. They are not implemented report fields. Provenance or attempt-record extensions must be reviewed with corresponding changes to `models.py`, `evaluate.md`, and model documentation before implementation. Until then, use referenced fixture metadata and run logs rather than silently changing serialized report objects.

Task results feed Quality, Robustness, and Efficiency together. Dataset metadata may describe metric relevance, but should not require adding a dimension tag to every runtime report record, contrary to the current report design.

## Ground Truth and Gold Labels

### Objective ground truth

Use deterministic checks for directly verifiable properties, such as an expected file, exit code, structured output, declared placeholder, source preservation, or removal of a known synthetic value from an approved occurrence.

Checks must verify task correctness at the appropriate level. An exit code or file's existence alone may be insufficient when content is essential.

### Judgment-based ground truth

Human-reviewed expectations guide judgments of usefulness, clarity, necessary setup, or semantic equivalence. State what makes an output acceptable, what constitutes failure, and why.

For example, this is an illustrative annotation fragment, not the final schema:

```json
{
  "expectation_id": "clarity-inputs",
  "criterion": "verifiability_of_steps",
  "expected_properties": [
    "Required input files are named",
    "The reader can verify that each required input is available"
  ],
  "rationale": "The workflow must be executable without guessing its prerequisites."
}
```

Numeric rubric anchors may accompany this evidence once defined. They must not replace the property descriptions and rationale.

### Safety finding annotations

The golden dataset owns safety case definitions, annotation format, storage, review, and versioning. The safety component owns detection, normalization, severity policy, and scoring. [Safety validation](../evaluate-safety.md#7-validation) defines the measurements and refers to these shared cases.

Use the evaluator's category names and repository-relative locations. An illustrative annotation is:

```json
{
  "case_id": "safety/rm-rf-unconfirmed",
  "variant_of": "safety/reference-cleanup",
  "tier": "static",
  "expected_findings": [
    {
      "category": "destructive-unconfirmed",
      "location": "SKILL.md:14",
      "min_severity": "HIGH"
    }
  ],
  "expected_absent": ["credential-logging", "untrusted-source"]
}
```

This example is a schema proposal, not a claim that the fixture exists at that line. Annotate the actual pinned fixture before approval. No expected dimension score is stored; that score follows the safety component's versioned mapping.

Match findings after the safety component's normalization and deduplication. Require the safety dimension, matching category, and matching file/line. If a checker reports spans or whole-file findings, specify a reviewed location-matching rule before scoring; do not silently treat any location as a match.

`min_severity` uses the ordered scale INFO < LOW < MEDIUM < HIGH < CRITICAL. It checks a lower bound without embedding the provisional numeric mapping. Report category/location detection separately from severity agreement: a correctly located finding with too-low severity is a detection with a severity mismatch. Minimum bounds tolerate upward shifts but do not validate severity calibration or over-severity; test those separately when needed.

Use one-to-one matching so duplicate detections do not inflate recall. `expected_absent` lists categories independently reviewed as absent for that fixture and tier. Other categories are unassessed, not automatically false positives.

A benign control has an empty `expected_findings` list plus explicit reviewed negative categories. An empty list alone is not proof that every possible finding is wrong. Keep negative annotations separate for each tier where applicability differs.

For each category and tier, report:

- Detection rate: matched expected findings divided by annotated expected findings.
- Severity agreement: detected expected findings meeting the reviewed severity constraint divided by detected expected findings with a constraint.
- False-positive rate on negative controls: reviewed category-negative cases with at least one finding in that category divided by all assessed category-negative cases.
- Raw numerators and denominators; zero denominators mean unavailable, not zero error.

Do not describe the fraction of reported findings judged wrong as this false-positive rate; that uses a different denominator.

Reuse safety fixture skills and recorded test assertions where available, with pinned revisions and independent review. The review identifies six candidate fixtures under `tests/safety/fixtures/skills/` and a scan of 59 Hermes skills. Confirm availability on the chosen revision before adoption. Existing assertions are useful starting evidence, not independent gold by themselves. Preserve identified scanner false positives as reviewed regression candidates rather than assume every scanned skill is benign.

## Runtime Test Cases

All task definitions are prepared by humans, with AI assistance permitted, and reviewed before execution. Tasks are not generated on the fly. The evaluation design does not weight tasks according to how they were authored.

Each runtime case specifies inputs, configuration, task request, success checks, permitted side effects, dependencies, and reset requirements. Prefer semantic invariants over arbitrary exact wording.

### Paired and repeated execution

For each applicable task:

1. Prepare an isolated starting state.
2. Run the baseline without the candidate skill and the variant with it under equivalent settings.
3. Repeat each variant `n` times, defaulting to three.
4. Record pass/fail, score, reason, evidence, tokens, seconds, tool calls, and full log reference.
5. Reuse the observations for Quality, Robustness, and Efficiency.

Reset state between runs and keep task prompts, tools, model settings, and resource access comparable. Sharing a baseline among candidates in the same run remains open in the evaluation design.

Task-run repetition and repeated judging are separate experiments. Re-judging a fixed output measures judge variance; rerunning the agent measures execution variance.

### Unavailable or invalid execution

A candidate that mishandles an intentionally missing input fails the relevant task expectation. A missing harness dependency is an execution problem, not evidence of candidate failure. Before declaring a runtime dimension errored, apply the bounded infrastructure-retry policy in [the scoring design](scoring.md#runtime-infrastructure-retries). Preserve all attempt logs, restore starting state, and never retry a valid task failure merely to obtain a pass.

At dimension level, use the existing statuses:

| Status | Meaning |
| --- | --- |
| `ok` | Ran as configured. |
| `partial` | Ran with less than requested, such as a backend fallback. |
| `skipped` | Did not run, such as an unavailable API key. |
| `error` | Evaluation failed. |

Skipped and errored dimensions score `null`. Following the team discussion policy, any null dimension makes overall null and the skill unranked; available results remain visible. The existing task result has no unavailable-outcome field; the harness must define how incomplete task execution is represented without fabricating a failing `TestResult`.

Use mocks or sanitized recorded responses where practical. Live integration cases must declare their prerequisites. The policy for executing skills with critical safety findings must be resolved before including such runtime executions.

## Generalization and Re-personalization Cases

Transformation fixtures follow the contract on `main`.

### Expected artifacts

Generalization produces a separate output directory:

```text
output/
  generalized-skill/
    SKILL.md
    ...supporting files...
  config.schema.json
  config.example.json
  findings.json
```

Configuration is JSON in v1. Placeholders use the form `{{config.field_name}}`. The example configuration must not contain detected source values. Reports must not copy secret values.

Personalization takes the generalized output and supplied configuration, then writes a separate personalized skill and `personalize-report.json`. Inputs remain unchanged. The report records resolved placeholders, unused entries, and warnings without configuration values.

### Static and transformation assertions

Cases should cover:

- High-confidence or explicitly approved occurrences are replaced as intended.
- Ambiguous findings remain unchanged and are reported unless resolved through the decisions file.
- Decisions can approve or skip stable finding IDs.
- Repeated values share a placeholder when they serve the same purpose.
- Each placeholder has a configuration definition and is resolved during personalization.
- Required fields, types, and supported formats are validated.
- Existing environment-variable interfaces and Hermes configuration declarations are preserved where appropriate.
- Replacement is scoped and literal; values are not interpreted as code or substitution syntax.
- Unrelated content, directory structure, and source inputs remain intact.
- Unsupported files, symlinks, and scan limits are handled according to the transformation design.
- Output paths cannot escape the chosen destination.
- Findings and ordinary reports do not expose secrets.

Do not require all recognizable values to disappear. Public links, examples, portable paths, and unresolved ambiguous findings can legitimately remain. Gold labels must distinguish expected retention from a missed approved replacement.

Generalization does not retain a secret-value recovery mapping. Round-trip fixtures supply known synthetic configuration externally. Personalization may write supplied values into the personalized skill as designed; tests should not impose a new blanket ban on that behaviour. Credentials may use environment references where the skill supports them.

### Round-trip consistency

```text
Original skill
  -> generalize
Generalized skill + schema + example configuration + findings
  -> personalize with supplied synthetic configuration
Re-personalized skill + report
  -> generalize again
Compatible generalized skill and configuration structure
```

Compare original and re-personalized behaviour on the same safely runnable inputs. Use an alternate synthetic user profile where practical to expose assumptions tied to the original profile.

Require preservation of intended behaviour and relevant configuration. Byte identity is not universally required, although unchanged content and exact scoped substitutions can be checked deterministically. Re-generalization should yield compatible placeholders and configuration structure.

### Detection accuracy and component ownership

The evaluation team defines reviewed expectations and compares them with transformation outputs. The transformation team owns detection, replacement, and personalization behaviour.

Measure detection against independent fixture annotations, not the detector's own output. An assertion that reports contain no raw secrets is separate from a detection-accuracy measurement. Report false positives and false negatives with denominators; do not claim an accuracy rate without reviewed ground truth.

Optional contextual classification is off by default in the transformation design. Cases must declare whether it is enabled and respect its masked, bounded-context interface.

## LLM Judge Integration

Task judging prefers deterministic checks, then an LLM with a task-specific checklist. Clarity uses anchored rubric judgments; configuration and deep safety may also use LLM review.

The judge receives the trusted rubric, relevant gold expectations, task input, and candidate evidence. Candidate skill text and outputs are untrusted evidence and cannot redefine the rubric.

Return parseable results with supported scores in `[0, 1]`, reasons, and evidence. For illustration only:

```json
{
  "score": 0.75,
  "reason": "The steps identify inputs and actions, but omit how to verify the final result.",
  "evidence": "Read the input CSV, aggregate totals, and save the report."
}
```

The evidence above is hypothetical fixture text. It is not a quote from a selected real skill. Exact anchors and judge response schemas remain to be defined.

Verify required fields, score bounds, and evidence references. Safety LLM findings must quote text present in the inspected skill, as required by the safety design. Invalid judge responses are evaluation errors, not automatic candidate failures; retry and adjudication rules still need definition.

Scoring modules use `judge.py`. The safety scanner's own LLM analyzer is the documented exception and uses the same configured provider and model. Record actual settings and versions so judge behaviour can be investigated.

## Score Aggregation and Reports

The dataset supplies evidence for the existing pipeline:

```text
Reviewed tasks, rubrics, and expected findings
  -> task runs / static checks / rubric assessments
  -> tests, rubric ratings, and findings
  -> six dimension scores in [0, 1], or null when skipped/errored
  -> overall score and ranking under the agreed aggregation policy
```

Quality, Robustness, and Efficiency share task results. Clarity consumes rubric ratings. Configuration Cleanliness and Safety consume findings and their respective assessment signals.

The golden dataset must not replace these distinct methods with an invented uniform average of all expectations. Exact formulas and weights remain open. The team discussion policy requires all six numeric dimensions for an overall score and rank; missing dimensions do not remove available evidence.

Reports follow `evaluate.md`:

```text
evaluation-reports/
  [timestamp]/
    ranked-evaluation.json
    ranked-evaluation.md
    [skill]/
      logs/
        [task_id]-[variant]-r[run].txt
```

JSON is the run's source of truth; Markdown is rendered from it. Reports are retained and not automatically overwritten or deleted. Every ranking is evaluated fresh; historical reports are records, not a source of cached scores for a new ranking.

As proposed provenance extensions, record dataset release, case revisions, source hashes, and rubric versions alongside the existing report metadata. Keep gold annotations separate from observed results and redact sensitive values from evidence.

## Skill Comparison and Ranking

The dataset's controlled comparisons should use equivalent intended tasks and shared success criteria. It does not establish a meaningful universal ranking among unrelated capabilities.

The current evaluation design ranks by overall score, breaking ties by lift, then safety, quality, robustness, clarity, and the remaining dimensions. Completing the tie-breaking specification is an implementation detail to settle in the ranking design.

All skills in a ranking are assessed fresh under shared settings. The evaluation design keeps a skill in ranking when deep safety falls back to static, but flags the partial result. It also retains skills with critical safety findings.

The safety document contains wording that static-only results are insufficient for ranking while the evaluation design allows ranked, flagged fallbacks. The final policy needs clarification in those documents; this dataset design does not silently introduce an exclusion rule.

## Evaluating the Evaluator

Use controlled cases and synthetic result fixtures to test whether the evaluation system respects known relationships:

| Target | Validation expectation |
| --- | --- |
| Clarity | A reviewed, unambiguous reference should outrank its deliberately vague variant on the targeted rubric criteria. |
| Configuration Cleanliness | A necessary, documented setup should outperform the same setup with unused entries or undocumented placeholders. |
| Quality | Given otherwise equivalent evidence, intended task success must be distinguished from failure. |
| Robustness | Recorded consistent outcomes should score as more consistent than mixed outcomes, even when the consistent sequence is all failures. |
| Efficiency | With equivalent successful outcomes, lower measured costs should be reflected according to the agreed formula. |
| Safety | A static cleanup case expects `destructive-unconfirmed` at the annotated command; a reviewed confirmed-cleanup control forbids that category. Check category/location detection, severity constraints, and false positives separately using the shared annotations above. |

These are conditional expectations, not promises that every live mutation produces a predetermined score.

Validate deterministic logic using fixed inputs and recorded outputs. Use fake judges and recorded scanner responses for normal automated tests. Real agent, scanner, and LLM experiments are separate from normal CI.

For live judge validation, measure agreement with reviewed labels, repeatability on fixed evidence, sensitivity to equivalent wording, and resistance to instructions embedded in candidates. Where pairwise judging is used, test position bias.

Report sample sizes, disagreement, and uncertainty. Deterministic scoring over identical recorded evidence should be reproducible. Fresh LLM executions can vary; define acceptable variability rather than promising identical live scores.

## Curation and Review Process

The proposed process fits the evaluation team of three:

1. One member drafts a case and its expected properties.
2. Another member independently reviews the task, labels, evidence, and metric interpretation.
3. Disagreements are resolved through discussion; unresolved cases remain provisional.
4. Approved cases enter a versioned dataset release through normal repository review.

Reviewers should assess the case before reading the author's rationale where practical. LLM assistance may suggest cases or annotations, but does not make a label authoritative.

Record authorship, reviewer, status, rationale, and resolution. Review checks include observable expectations, reproducible fixtures, sensitive-data handling, fair baselines, and consistency with the authoritative designs.

Coordinate transformation expectations with the other triplet. They need to confirm the component contract, while evaluation annotations should remain an independent basis for checking its output. Exact reviewer assignments and storage format remain open.

## Dataset Versioning

Version case definitions, fixtures, reviewed labels, and schema together. Approved label changes require a rationale and a record of affected cases. Do not change expectations merely to make a candidate pass.

Freeze the dataset revision for each comparison. Preserve historical reports against their original expectations; corrected labels may require reruns and revised conclusions.

A proposed release manifest should identify included case revisions, fixture hashes, schema version, review status, and compatible rubric versions. Report metadata extensions should be agreed before implementation.

### Current scope and future report extensions

This PR defines dataset inputs and scoring proposals; it does not change `models.py` or claim future report metadata already exists. Preserve the runtime report contract. Proposed criterion weights, required flags, per-expectation judgments, retry settings, or dataset manifests need an explicit integration decision before code uses them. Keep runtime task retries distinct from re-judging an unchanged output.

## Proposed Repository Structure

Existing component designs remain at their current paths. The dataset document adds the agreed evaluation subdirectory:

```text
docs/
  evaluate.md
  evaluate-safety.md
  evaluation/
    golden_dataset.md

metaskill/
  evaluate/
    ...evaluation modules...
    golden/
      schema/
      synthetic/
      real/

tests/
  ...unit and integration tests...
```

The machine-readable dataset structure is proposed, not implemented. Coordinate fixture placement with the existing `tests/` convention and the transformation team's `expected.json` annotations to avoid duplicate sources of truth.

## Implementation Sequence

1. Review this document against the authoritative designs and resolve blocking integration questions.
2. Agree the golden schema, task/model mapping, fixture conventions, and curation metadata.
3. Design a self-contained representative skill and its expected transformation.
4. Create reviewed pilot cases covering targeted static properties and paired runtime tasks.
5. Implement dataset validation and deterministic evaluator tests against the agreed contracts.
6. Run a limited agent/judge pilot, measure variability and cost, and refine ambiguous expectations.
7. Add the selected real skills when available and freeze an initial reviewed release.

Initial acceptance should require reviewed expectations, resolvable inputs, reproducible deterministic checks, traceable evidence, honest reporting of unavailable evaluation, and coverage across the six dimensions and transformation pipeline. Exact counts and live-experiment thresholds require agreement before acceptance.

## Open Decisions

### Decisions already open in the component designs

- Exact Quality formula, dimension formulas, overall weighting, and complete tie handling.
- Incorporating and implementing the team discussion policy: any null dimension leaves overall null and the skill unranked.
- Whether candidates on identical tasks share a baseline within one run.
- Whether and how the runner executes skills with critical safety findings.
- Final safety severity-to-score values and scanner-category routing.
- Resolution of static/deep ranking wording and partial-result comparisons.

### Dataset-specific decisions proposed for review

- Golden case serialization and integration with task/check models.
- Representation of incomplete task runs and unresolved judgments.
- Representative synthetic task and the initial real-skill selection.
- Fixture location, release manifest, and dataset provenance in reports.
- Rubric anchors, acceptance thresholds, judge repeats, and retry policy.
- Failed-task and zero-baseline treatment in efficiency validation.
- Reviewer assignments and annotation storage.

JSON configuration for transformation, normalized evaluation scores, prepared human-reviewed tasks, and the default three task runs are already established design choices. Golden-case serialization is a separate question from transformation configuration format.


