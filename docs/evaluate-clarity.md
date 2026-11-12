# Clarity Evaluation

Status: draft. Code: [`metaskill/evaluate/clarity.py`](../metaskill/evaluate/clarity.py). Parent design: [evaluate.md](evaluate.md), section 3.3. Scoring context: [evaluation/scoring.md](evaluation/scoring.md#clarity).

Clarity answers: "How clear is the skill to the agent and to a person reading it?" It is judged by an LLM against a fixed rubric. No task is run.

## 1. Rubric

Two rulesets existed: the five criteria in evaluate.md 3.3 (trigger specificity, step ordering, verifiability, failure handling, human followability) and four criteria for what a clear skill reads like (concise, understandable, readable, clearly framed and structured). They are merged into seven criteria, weighted equally:

| Criterion | Question | From |
|---|---|---|
| `purpose_and_trigger` | Does the skill state the problem it solves, when to use it, and when not to? | Framing (problem part) + trigger specificity |
| `conciseness` | Does it say what it needs to without padding, repetition or long preambles? Code the agent must run is content, not padding. | Concise |
| `accessible_language` | Can a reader with basic subject knowledge follow it without a glossary? Jargon, acronyms and scientific names are avoided or explained at first use. | Understandable |
| `readability` | Could a person tell what it accomplishes, and follow it, without an AI explaining it? | Readable + human followability |
| `structure_and_flow` | Are sections organised, steps in the order they must be done, and does the writing flow? | Framing (structure part) + step ordering |
| `verifiability` | Can the reader check inputs, intermediate results and completion? | Verifiability |
| `failure_handling` | Does it say what to do when relevant things go wrong? A simple workflow whose stopping conditions are already clear earns full credit without a separate section. | Failure handling |

Each criterion has five anchored levels, worst first, normalised to `0`, `0.25`, `0.5`, `0.75` and `1` (scoring.md proposal). The anchor text lives in `CRITERIA` in `clarity.py`, which is the single source; `RUBRIC_VERSION` is bumped whenever a criterion, anchor or the instructions change, since ratings from different versions do not compare.

**[OPEN]** Equal weights. Verifiability and failure handling are about the workflow more than the writing; they could weigh less, or move to Quality.

## 2. Scoring

```text
C = (sum of the seven criterion ratings) / 7, with an unrated criterion counted as 0
```

| Outcome | Status | Score |
|---|---|---|
| All seven criteria rated | `ok` | `C` |
| Some criteria have no usable rating | `partial` | `C`, with the missing criteria named in `detail` |
| No usable rating at all | `error` | `null` |
| No model available (no `ctx.llm`, no provider) | `skipped` | `null` |

A rating is unusable when the judge skipped it, rated it twice, rated it out of range, or gave a quote that is not in the skill (judge.md 2.2). Counting it as 0, rather than averaging over the rated criteria, is deliberate: the judge most often fails to quote a weakness that is an *absence* (no failure handling), so averaging would reward unclear skills. The instructions tell the judge to quote the nearby text for an absence.

**[OPEN]** Missing as 0 versus mean of rated criteria; and whether a `partial` Clarity score should be ranked (scoring.md says other partial results need a documented rule: this is that rule, pending agreement).

## 3. Implementation

### 3.1 `clarity.py`

| Name | Description |
|---|---|
| `CRITERIA` | The seven `Criterion(name, question, levels)` objects, in report order |
| `INSTRUCTIONS` | Trusted judge instructions. Holds no text from the skill (judge.md 2.2). Says: judge the writing only; code blocks are judged on explanation, not length; longer is never clearer; quote nearby text for an absence; rate criteria independently |
| `RUBRIC_VERSION` | Version of the rubric above |
| `score(skill, config, llm=None, judge=None)` | Calls `judge.rate(skill, CRITERIA, INSTRUCTIONS)` and returns `score_from_ratings`. Builds a `Judge` on `llm` if no shared judge is given; raises `JudgeUnavailable` if neither is |
| `score_from_ratings(ratings)` | The `DimensionScore` of section 2. `reason` gives the number rated and the weakest criterion; `rubric` keeps every rating with its quote |

What the judge sees is decided by `Judge.rate`: `SKILL.md`, scripts and other text files, hidden text stripped so a comment aimed at the judge cannot sway a rating, cut to `judge_max_chars` (judge.md 2.3).

### 3.2 Wiring into evaluate

The plugin's `evaluate` and `rank` commands (`metaskill/evaluate/__init__.py`) already score each dimension by importing its module from `SCORERS`. With `clarity.py` present, Clarity is no longer `skipped: not built yet`. Changes there:

- One `Judge` is built per call with `Judge.from_context(ctx, config)` and passed as `judge=` to the dimensions in `JUDGED` (Clarity for now; Configuration Cleanliness and the safety review later). Reusing one judge is what makes `judge_model` complete (judge.md 1.1).
- `ReportMeta.judge_model` is now the judge's record of which models answered, instead of always `""`. It stays `""` when no judge answered.
- `JudgeUnavailable` from Clarity becomes `skipped: no judge model is available`; any other failure, including `JudgeError`, becomes `error`, as for every dimension.

Clarity makes one judge call per skill (plus `judge_retries` on a malformed reply), with the skill's text as input: roughly the skill's size in tokens plus about 2,000 for the rubric.

## 4. Golden dataset (temporary)

**Location is temporary.** The set lives in `tests/fixtures/golden/clarity/` until the team agrees where the golden dataset is stored ([golden_dataset.md](evaluation/golden_dataset.md), proposed repository structure). It is unreviewed and for analysis only.

```text
tests/fixtures/golden/clarity/
  cases.json          one case per skill: expected properties, verbatim quotes, rationale
  skills/<name>/      the skill (SKILL.md with Hermes front matter)
tests/golden_clarity.py       loads the set; compares a live report against it
tests/test_golden_clarity.py  structural checks
```

Annotations live outside the skill folders, so a judge reading a skill never sees its label.

### 4.1 Cases

Ten clear skills (positive controls, `clarity-high-*`), each annotated on all seven criteria, and ten unclear variants (`clarity-low-*`), one per clear skill. A variant names its twin in `variant_of` and the criteria it degrades in `targeted_criteria`, and is annotated only on those.

| Clear skill | Edge case it probes | Unclear variant degrades |
|---|---|---|
| csv-dedupe | Inline script should not count as wordiness | conciseness, purpose_and_trigger, verifiability (padded essay; overwrites input) |
| git-undo-last-commit | Risky operation explained without jargon | accessible_language, readability (Git internals jargon) |
| meeting-action-items | Pure writing task, no tools | purpose_and_trigger, structure_and_flow, readability, failure_handling (sections reversed; invents owners) |
| backyard-bird-log | Jargon-prone domain; common names over scientific names | accessible_language, readability (Latin names, plumage terminology) |
| image-resize-web | Prerequisites before steps | structure_and_flow only (same content, shuffled) |
| pdf-merge | Very short; brevity is not penalised | purpose_and_trigger, readability, verifiability, failure_handling (two-line skill) |
| ab-test-readout | Unavoidable terms, each defined at first use | accessible_language, readability (same method, statistical notation) |
| recipe-scaling | Failure cases under a non-standard heading | conciseness, failure_handling (padded essay) |
| markdown-link-check | Longest script; second code-length check | verifiability, failure_handling (swallows every error) |
| monthly-spending-review | Domain term (debit) used once where the data uses it | purpose_and_trigger, structure_and_flow, readability (one run-on paragraph) |

The `image-resize-web` and `ab-test-readout` variants keep their twin's content and change one thing (order; jargon), so they isolate a single criterion.

### 4.2 Structural checks (CI)

`tests/test_golden_clarity.py`, offline:

- The dataset's criteria and `rubric_version` match `clarity.py`.
- Ten clear cases, ten variants, one variant per clear case; every skill folder has a case.
- Clear cases cover all seven criteria; variants annotate exactly their targets.
- Every quote appears verbatim in its skill; each front-matter `name` matches its folder.
- Clear skills stay at or under 1000 words. This is a loose sanity bound, not a measure: several unclear variants are shorter than their clear twins.

### 4.3 Live check in the Hermes CLI

Real judge runs stay out of CI (evaluate.md 6). To run one:

1. From the repository root, print the command: `py tests/golden_clarity.py --command`. It is `/metaskill rank tests/fixtures/golden/clarity/skills/csv-dedupe, ...` with all twenty skills.
2. Run it in Hermes. The report folder is written under `evaluation-reports/`.
3. Compare: `py tests/golden_clarity.py evaluation-reports/<run>/ranked-evaluation.json`.

The comparison prints one row per pair and exits non-zero unless, for every pair, the clear skill has the higher Clarity score **and** a strictly higher rating on every targeted criterion (an unrated criterion counts as 0). This is the "reference outranks its vague variant" check of golden_dataset.md, *Evaluating the Evaluator*. Clear-skill scores are not given a threshold yet.

## 5. Open questions

1. Equal weights across the seven criteria (section 1).
2. Unrated criterion as 0, and ranking of a `partial` Clarity score (section 2).
3. Permanent location and schema of the golden set (section 4); depends on where the rest of the golden dataset is stored.
4. Whether to rate more than once and take the median, once live variability on the golden set is measured.
5. A threshold for clear skills (for example, every clear skill at or above 0.75), once a few live runs show the spread.
