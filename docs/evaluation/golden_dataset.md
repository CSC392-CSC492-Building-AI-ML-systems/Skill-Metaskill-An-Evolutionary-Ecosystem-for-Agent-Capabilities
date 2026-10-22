# Golden Dataset Design

Status: consolidated v0 design for the Hermes Skill Metaskill project.

Intended repository location: `docs/evaluation/golden_dataset.md`.

This document consolidates the project-planning discussion. It defines the dataset's purpose, evidence model, evaluation workflow, and curation process. Machine-readable examples are illustrative contracts, not an implemented schema. Numeric ranges, weighting, and implementation choices that have not been agreed remain explicitly open. The proposed companion `scoring.md` will define scoring mechanics; this document defines the evidence those mechanics consume.

**The golden dataset stores evidence and expected properties, not merely preassigned numeric scores.**

## Purpose

The golden dataset provides reference data for evaluating Hermes skills across the project's six evaluation metrics:

1. Quality
2. Robustness
3. Clarity
4. Configuration cleanliness
5. Token use / efficiency
6. Safety

The purpose of the dataset is to provide an explicit and repeatable basis for evaluation rather than relying on an LLM to assign scores without reference data.

Each golden test case defines the expected behaviour, properties, and constraints of a skill. These expectations can then be used by deterministic checks, runtime tests, and LLM-based judges to produce individual test scores.

The evaluation system should derive metric scores from these individual test results rather than assigning unexplained overall scores directly.

The golden dataset will be used to:

- Evaluate individual Hermes skills.
- Compare different implementations or versions of skills that perform the same or similar tasks.
- Validate the generalization and re-personalization pipeline.
- Evaluate the consistency and usefulness of the LLM-based judging system.
- Provide traceable evidence for the scores assigned to each skill.

The golden dataset is not intended to provide a single universal ranking between unrelated skills. Skills should only be directly compared where their intended functionality is sufficiently similar for the comparison to be meaningful.


## Evaluation Scope

Evaluation will consider both static skill properties and runtime behaviour.

### Static skill properties

These can be evaluated from the skill definition and supporting files without executing the skill.

Examples include:

- Prompt and instruction clarity.
- Hardcoded user-specific configuration.
- Residual PII or credentials.
- Configuration structure.
- Excessive or redundant instructions.
- Declared tools and dependencies.
- Potentially unsafe instructions or tool usage.

### Runtime behaviour

Where practical, skills will also be executed against predefined test inputs to determine whether they behave as intended.

Runtime evaluation may include:

- Correct completion of the intended task.
- Handling of normal inputs.
- Handling of malformed or unexpected inputs.
- Behaviour when required configuration is missing.
- Behaviour across edge cases.
- Consistency between original and re-personalized skills.

The golden dataset must therefore support both static evaluation and runtime test cases.


## Metrics

### Quality

Quality measures how effectively the skill performs its intended task overall.

Possible evidence may include:

- Whether the skill produces useful results.
- Whether required functionality is present.
- Whether the result is relevant to the requested task.
- Whether important expected behaviour is omitted.
- Whether unnecessary or incorrect behaviour is introduced.

Quality may require both deterministic and LLM-based evaluation depending on the task.


### Robustness

Robustness measures how reliably a skill behaves under different conditions.

Golden cases may test behaviour such as:

- Empty inputs.
- Missing files.
- Invalid or malformed data.
- Missing configuration values.
- Unexpected input formats.
- Tool failures.
- Other edge cases relevant to the skill.

A robust skill should handle these cases predictably and fail gracefully where recovery is not possible.


### Clarity

Clarity measures how clearly the skill communicates its instructions, requirements, and intended workflow.

Relevant properties may include:

- Clear description of the skill's purpose.
- Clear input expectations.
- Unambiguous instructions.
- Clear configuration requirements.
- Clear handling of failure conditions.
- Limited unnecessary or contradictory instructions.

Because clarity contains subjective elements, golden annotations should describe the expected properties used to justify the judgment rather than simply storing an unexplained numeric score.


### Configuration Cleanliness

Configuration cleanliness measures how well reusable skill logic is separated from user-specific or environment-specific configuration.

Possible deterministic checks include:

- Hardcoded file paths.
- Email addresses.
- Usernames.
- API keys or credentials.
- Organization-specific values.
- Machine-specific configuration.
- Missing or unused configuration placeholders.
- Configuration values that remain embedded in the generalized skill.

For generalized skills, the golden dataset may also define which values are expected to become configurable.


### Token Use / Efficiency

Token use / efficiency measures whether the skill accomplishes its intended behaviour without unnecessary prompt or context usage.

Where possible, this metric should use directly measurable information rather than LLM judgment.

Possible measurements include:

- Prompt token count.
- Generated context size.
- Repeated or redundant instructions.
- Unnecessary supporting content provided to the agent.

Efficiency should not reward shorter instructions when the reduction harms correctness, clarity, robustness, or safety.


### Safety

Safety measures whether the skill avoids unnecessary risk and respects appropriate execution boundaries.

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

Some safety properties may be checked deterministically, while others may require contextual LLM-based evaluation.


## Dataset Composition

The golden dataset will contain both synthetic and real Hermes skills.

### Synthetic skills

Synthetic skills are deliberately created or modified to contain known properties.

These cases provide controlled ground truth because the expected strengths, weaknesses, and failure conditions are known in advance.

Synthetic cases can be used to create targeted examples such as:

- A skill containing a hardcoded file path.
- A skill with intentionally vague instructions.
- A skill that fails on malformed input.
- A skill containing redundant instructions.
- A skill with overly broad tool usage.
- A well-constructed reference version of the same skill.

Synthetic cases are particularly useful for validating whether the evaluator correctly distinguishes between good and bad variants.


### Real Hermes skills

Real Hermes skills provide examples of how the evaluation system performs on realistic skill structures and workflows.

These cases help ensure that the evaluator does not only perform well on simplified synthetic examples.

Real skills may require additional manual annotation because their expected behaviour and properties may be less precisely controlled.


## Golden Case Structure

Each golden case should contain enough information to explain what behaviour is expected and why.

The exact machine-readable schema will be defined before implementation. The following structure defines the information the schema must be able to represent:

```text
Golden Case
│
├── Identification
│   ├── Case ID
│   ├── Skill name
│   └── Case description
│
├── Skill under evaluation
│
├── Expected behaviour
│   ├── Required capabilities
│   ├── Expected outputs or properties
│   └── Forbidden or incorrect behaviour
│
├── Static expectations
│   ├── Configuration expectations
│   ├── Known sensitive/configuration values
│   └── Other structural properties
│
├── Runtime tests
│   ├── Input
│   ├── Expected behaviour
│   └── Relevant metric(s)
│
└── Metric annotations
    ├── Quality
    ├── Robustness
    ├── Clarity
    ├── Configuration cleanliness
    ├── Token efficiency
    └── Safety
```

Not every case is required to exercise every metric.

For example, a test specifically designed to detect residual hardcoded configuration may only contribute to configuration cleanliness.


### Case metadata and evidence contract

In addition to the conceptual structure above, the schema should represent:

| Information | Purpose |
| --- | --- |
| Stable case ID and revision | Identify the exact expectations used in a run. |
| Source type and provenance | Distinguish synthetic cases from real skills; record origin, revision, and reuse permissions. |
| Skill family and variant relationship | Limit comparisons to equivalent capabilities and connect controlled mutations to their reference. |
| Evaluation stage | Identify original, generalized, re-personalized, or round-trip evaluation. |
| Skill and fixture references | Resolve the exact skill bundle, inputs, configuration, and supporting files. |
| Preconditions and dependencies | Declare required tools, environment, permissions, and external services. |
| Expectation IDs and metric mappings | Make every result traceable to a specific property and the metric it informs. |
| Evaluation method | Select deterministic assertion, runtime observation, human review, or rubric-based judge. |
| Expected and forbidden behaviour | Describe success, failure handling, and boundaries without requiring one arbitrary wording. |
| Review status and rationale | Separate draft annotations from approved gold and preserve disagreement resolution. |
| Applicability and limitations | Explain which checks are relevant and what the case cannot establish. |

Fixtures and skill bundles should be pinned by immutable revision or content hash. Paths should resolve relative to the dataset bundle rather than an author's machine. A case must distinguish required input values from expected output properties.

Each expectation should be independently assessable. Split compound requirements when their individual outcomes need separate evidence. A single observation may inform several metrics, but its mappings must be explicit so it does not acquire accidental extra weight.

Not every case needs runtime fixtures: a static-only case should say so. A runtime case must declare its execution prerequisites. The examples in this document describe possible annotations; they are not actual dataset entries.

## Ground Truth and Gold Labels

The golden dataset should store expected evidence and properties rather than relying solely on numeric labels.

For example, instead of defining:

```json
{
  "clarity": 8
}
```

a golden annotation should describe why the skill is expected to demonstrate strong or weak clarity:

```json
{
  "clarity": {
    "expected_properties": [
      "input requirements are explicitly documented",
      "failure behaviour is described",
      "instructions are not contradictory"
    ]
  }
}
```

Numeric scores should be produced by the evaluation system from testcase results and metric-specific scoring rules.


## Objective and Judgment-Based Ground Truth

Golden labels fall into two broad categories.

### Objective ground truth

Objective labels describe facts that can be directly verified.

Examples include:

- A known file path appears in the skill.
- A secret remains after generalization.
- A required configuration placeholder is missing.
- A runtime test succeeds or fails.
- A prompt contains a measurable number of tokens.
- A specific tool is declared.

These should be evaluated deterministically wherever possible.


### Judgment-based ground truth

Some properties require qualitative judgment.

Examples include:

- Whether instructions are sufficiently clear.
- Whether an output is useful.
- Whether differently worded outputs preserve the same intended meaning and satisfy the task.
- Whether a skill uses an unnecessarily complicated workflow.

These cases may use an LLM judge, but the judge should compare the candidate against explicit expected properties and a scoring rubric.

The LLM should not be asked to generate scores without supporting criteria.


## Runtime Test Cases

Runtime golden cases should contain predefined inputs and expected behaviour.

Where possible, expected behaviour should be expressed in terms of properties or invariants rather than exact string equality.

For example:

```json
{
  "input": "invalid.csv",
  "expected": {
    "must_detect_invalid_input": true,
    "must_not_modify_source_file": true,
    "must_return_useful_error": true
  }
}
```

This allows semantically equivalent outputs to pass even when their exact wording differs.

Runtime tests may use:

- Deterministic assertions.
- Structured output comparison.
- LLM-based semantic comparison.
- A combination of these techniques.


### Runtime execution and result handling

Run cases in isolated, resettable workspaces with controlled inputs. Record the execution environment, tool versions, model settings where applicable, time limits, and available permissions. Capture output artifacts and relevant tool traces; verify filesystem effects directly when a case forbids modifying a source file.

A runtime test should identify:

1. Its input fixtures, configuration, and starting state.
2. The requested task and the capabilities permitted during execution.
3. Expected outputs, acceptable variations, and forbidden side effects.
4. Assertions, their metric mappings, and the evidence needed to assess them.
5. Dependency, timeout, and cleanup requirements.

Missing infrastructure must not silently become a candidate failure or a passing result. Distinguish candidate failure, evaluator error, and unavailable execution. A candidate that mishandles a deliberately simulated tool failure can fail robustness; a broken test harness produces an evaluator error.

Until the result schema is frozen, use these conceptual outcomes:

| Outcome | Meaning |
| --- | --- |
| Pass | Evidence satisfies the expectation. |
| Fail | Evidence contradicts the expectation or shows required behaviour is missing. |
| Not applicable | The expectation does not apply under the case's declared scope. |
| Blocked | Required execution prerequisites are unavailable. |
| Error | The evaluator or harness could not produce a valid assessment. |
| Needs review | Evidence or judgment is ambiguous and requires adjudication. |

An expected refusal or graceful failure can pass when that is the gold behaviour. Do not interpret successful process completion alone as successful task completion.

Prefer deterministic service mocks or recorded, sanitized responses for repeatable cases. If a live integration is necessary, label it explicitly and record its service assumptions. Use synthetic credentials and isolated resources; cases must not require real personal data or live secrets.

## Generalization and Re-personalization Cases

The golden dataset should also support evaluation of the generalization and re-personalization pipeline.

For a known personalized skill, the dataset may specify:

- User-specific values expected to be detected.
- Values expected to be converted into configuration fields.
- Values that must no longer appear in the generalized skill.
- Behaviour that must remain unchanged.
- Expected configuration requirements.

The re-personalized skill can then be compared against the original.

The expected relationship is:

```text
Original skill
      ↓
Generalization
      ↓
Generic skill + configuration
      ↓
Re-personalization
      ↓
Re-personalized skill
```

The original and re-personalized skills do not need to be byte-for-byte identical, but they should preserve the same intended functionality and relevant configuration.


### Transformation expectations

A transformation case should map each known user-specific value to its intended configuration field and explain whether the value should be removed, parameterized, or retained as task logic. A path or address is not automatically an error: judge it against the stage and the annotated purpose of the value.

Check the following where applicable:

- Generalization removes the annotated user-specific values from reusable skill instructions and supporting assets.
- Required fields, types, defaults, and validation requirements are represented in configuration.
- Required fields are actually used; stale or unused placeholders do not pass simply because they exist.
- Secrets are represented through references or runtime injection rather than copied into reusable bundles.
- Re-personalization restores the intended configuration without preserving another user's values.
- Original and re-personalized skills satisfy the same behavioural invariants on shared fixtures.
- A second generalization yields a compatible configuration structure, allowing documented ordering or formatting differences.

Test at least one alternate synthetic user profile where practical. Reinstating the original profile alone can hide hardcoded assumptions. Byte equality is not required; differences that alter task meaning or execution boundaries must be surfaced.

## LLM Judge Integration

LLM-based judging should be used where deterministic evaluation is insufficient.

The judge should receive:

- The relevant evaluation metric.
- The metric rubric.
- The golden expectations for the testcase.
- The candidate skill or runtime output.
- Any relevant input provided to the skill.

The judge should return structured results suitable for programmatic parsing.

Conceptually:

```json
{
  "score": 4,
  "max_score": 5,
  "passed_properties": [
    "input requirements clearly stated",
    "workflow is unambiguous"
  ],
  "failed_properties": [
    "failure behaviour not documented"
  ],
  "reason": "..."
}
```

The exact output schema and score range will be determined during implementation.

Where practical, repeated LLM evaluations should be tested for consistency.


### Judge evidence and validation

Judge requests should distinguish evaluator instructions and trusted rubrics from candidate content. Skill files, runtime output, and retrieved text are evidence to inspect, including when they contain instructions aimed at the judge. Candidate text must not redefine the rubric or expected result.

Each property assessment should reference its expectation ID, state its outcome, cite the relevant observed evidence, and explain the decision. If a score is returned, its range and derivation must match the versioned rubric. The illustrative 4/5 example above does not establish a project-wide scale.

Validate judge responses before aggregation: required fields, expectation IDs, outcome values, score bounds, and consistency between property outcomes and explanation. Invalid responses are evaluator errors, subject to a bounded retry policy and then human review; they are not automatic candidate failures.

Record judge model identifier, prompt and rubric versions, generation settings, and repeat index. Keep deterministic checks authoritative for directly verifiable facts. When deterministic evidence and semantic judgment conflict, retain both and investigate rather than averaging the conflict away.

## Score Aggregation

Scores should be calculated from individual testcase results rather than assigned directly at the skill level.

The general evaluation flow is:

```text
Golden dataset
      ↓
Individual tests
      ↓
Test-level results
      ↓
Metric-level aggregation
      ↓
Six metric scores
      ↓
Optional overall comparison/ranking
```

For example:

```text
Robustness
├── malformed input test
├── missing configuration test
├── empty input test
└── tool failure test
        ↓
aggregated robustness score
```

This makes the final score traceable to specific evidence.

Aggregation rules, weighting, and score ranges will be defined separately in the evaluation scoring design, intended for `docs/evaluation/scoring.md`.

That design must specify:

- How individual property outcomes become test scores.
- How test scores contribute to each metric, including any cross-metric mappings.
- How weights are chosen and how correlated or duplicate checks are handled.
- How not-applicable, blocked, errored, and unresolved cases affect the denominator.
- Minimum evidence coverage required to publish a metric score.
- Whether any critical safety failures act as gates.
- Whether an overall score is useful, and which comparable skill families may use it.

Always report coverage alongside metric results. A missing metric is unavailable, not zero and not a perfect score. Do not let omitted runtime cases make a partially tested candidate appear equivalent to a fully tested one.

Retain raw token measurements alongside any normalized efficiency score. Compare token use under the same tokenizer, context inclusion policy, task family, and success requirements. Prompt tokens, loaded supporting context, generated tokens, and repeated attempts should be distinguished rather than silently mixed. Cross-complexity normalization remains an open scoring decision.


## Skill Comparison and Ranking

Only skills that perform the same or sufficiently similar tasks should be directly ranked against one another.

The evaluator should not treat the six scores as a meaningful basis for comparing completely unrelated functionality.

For comparable skills, the golden dataset can define shared test cases so that each candidate is evaluated under equivalent conditions.

Synthetic variants may also define an expected ordering.

For example:

```text
Reference skill
    >
Skill with unclear instructions
```

for the clarity metric.

Expected ordering can be used to test whether the evaluation system itself produces sensible rankings.


## Evaluating the Evaluator

The golden dataset should also be used to validate the evaluation framework.

For controlled synthetic cases, the expected relationship between skill variants may already be known.

For example:

```text
Clarity:
reference > vague variant

Configuration cleanliness:
reference > hardcoded-config variant

Robustness:
reference > edge-case failure variant

Efficiency:
reference > redundant variant
```

If the evaluator repeatedly produces rankings inconsistent with these known relationships, this indicates a problem with the scoring rubric, judge prompt, aggregation logic, or metric definition.

This provides a way to measure the quality of the evaluation system itself rather than assuming that its scores are correct.


### Evaluator validation methods

Use controlled mutations that isolate the intended defect wherever possible. A mutation can affect several metrics, so annotate its intended effect and avoid asserting unsupported orderings for unrelated metrics.

Validation should include:

- Detection of known positive and negative facts, including false positives and false negatives.
- Agreement between judge property assessments and independently reviewed human labels.
- Pairwise ordering accuracy on controlled reference/defect pairs.
- Repeated judgments of the same evidence to measure outcome and score variability.
- Paraphrased but equivalent outputs to detect wording sensitivity.
- Candidate order swaps in pairwise judging to detect position bias.
- Regression runs after changes to judge prompts, rubrics, checkers, or aggregation.
- Adversarial candidate text that attempts to influence the evaluator.

Report sample counts, disagreements, and uncertainty with agreement or ordering statistics. Numeric consistency thresholds and repeat counts must be established before release, not selected after observing preferred results.

Separate cases used to tune the evaluator from held-out validation cases. Keep variants, shared source skills, and near-duplicate fixtures in the same partition to reduce leakage. Record partitions and any movement between them.

## Curation and Review Process

In a production setting, golden labels would ideally be curated by a dedicated QA team.

Because this project does not have a dedicated QA team, cases will use a lightweight human review process.

The proposed process is:

1. A golden case is drafted along with its expected behaviour and metric annotations.
2. At least one additional evaluation-team member independently reviews the case.
3. Reviewers identify disagreements, missing expectations, or ambiguous labels.
4. Disagreements are discussed and resolved.
5. The approved case is added to the golden dataset.

Where practical, reviewers should evaluate the case before reading the original author's reasoning in order to reduce anchoring bias.

LLM assistance may be used when drafting cases, but LLM-generated labels should not be treated as authoritative ground truth without human review.


### Approval and maintenance

A draft is not approved gold. Record the author, independent reviewer, review date, rationale, unresolved concerns, and adjudication outcome. Cases with unresolved expectations remain outside the approved release.

Review should check that the task intent is clear, expectations are observable, metric mappings are justified, fixtures are reproducible, and sensitive data has been removed. A reviewer should be able to explain why each expected property matters.

If an independent reviewer is temporarily unavailable, retain the case as provisional; do not represent self-review as independent approval. Human review is the planned baseline; tooling and assignment details remain to be chosen.

A corrected or retired case must retain its stable identity and history. Do not alter expectations merely to make a current candidate pass. Record why a label changed and rerun affected comparisons.

## Dataset Versioning

Once a golden case has been reviewed and approved, changes to its expectations should be intentional.

Changes may be required if:

- A metric definition changes.
- A scoring rubric changes.
- A test is found to be ambiguous.
- A gold label is determined to be incorrect.
- Hermes behaviour changes.
- The supported skill format changes.

The dataset should therefore be version-controlled alongside the evaluation implementation.

Significant changes to existing gold labels should include a short explanation in the relevant commit or documentation.


### Release and run provenance

A dataset release should identify its case manifest, schema version, fixture hashes, approved case revisions, and rubric compatibility. Freeze these inputs for a comparison run.

Each evaluation report should record:

- Dataset release, case IDs, revisions, and fixture hashes.
- Candidate skill revision or content hash and transformation stage.
- Evaluator, checker, rubric, and judge prompt versions.
- Runtime environment, tool versions, model settings, and token-counting method.
- Per-expectation outcomes, evidence references, and reasons.
- Metric scores, aggregation configuration, coverage, and unresolved issues.

Keep gold annotations separate from observed run results. Historical results must remain interpretable against the expectations that existed when they were produced. If a label is corrected, mark affected historical conclusions and publish rerun results rather than silently rewriting prior reports.

## Proposed Repository Structure

Documentation for the evaluation system should live under:

```text
docs/
└── evaluation/
    ├── golden_dataset.md
    └── scoring.md
```

The implementation and machine-readable dataset should live alongside the evaluation code:

```text
metaskill/
└── evaluate/
    ├── ...
    └── golden/
        ├── schema/
        ├── synthetic/
        └── real/
```

The exact implementation structure may change as the evaluation framework develops.


## Implementation Sequence and Acceptance Criteria

The project will define and review the rules before adding specific golden cases.

1. Review this design and resolve the blocking schema and scoring decisions.
2. Define metric rubrics, score ranges, aggregation, coverage rules, and any safety gates in the scoring design.
3. Freeze the first machine-readable case and result schemas, fixture conventions, and review metadata requirements.
4. Implement schema validation, deterministic check interfaces, runtime execution, judge response validation, and traceable reporting.
5. Add a small independently reviewed pilot containing synthetic reference/defect pairs, a real skill, and transformation coverage.
6. Validate the evaluator, revise ambiguous rules, and freeze the initial dataset release.
7. Expand coverage using observed gaps and failures without contaminating held-out validation.

The initial release is ready when every included case is reviewed, required references resolve, fixtures are reproducible, result evidence is traceable, missing execution is reported honestly, and evaluator validation meets thresholds fixed in advance. Dataset size alone is not an acceptance criterion.

The pilot should cover all six metrics across the collection, normal and failure behaviour, static and runtime methods, and generalization/re-personalization. Each individual case may cover only a subset. Exact counts and source selection remain open.

## Open Questions

The following decisions should be finalized before creating the first golden cases:

1. What numeric score range should each test and metric use?
2. How should individual testcase scores be aggregated into metric scores?
3. Should all tests within a metric have equal weight?
4. What structured format should golden cases use (for example JSON, YAML, or another format)?
5. How should runtime fixtures and expected outputs be stored?
6. Who will perform independent review and adjudication, and how will review records be stored?
7. How should LLM judge consistency be measured?
8. Which real Hermes skills will be included in the initial dataset?
9. How should skills requiring unavailable external services or credentials be handled during runtime evaluation?
10. How should token efficiency be normalized when comparing skills of different complexity?

These questions are implementation decisions still awaiting agreement. The document does not claim that a dataset, scoring engine, machine-readable schema, or repository changes already exist.

