# Clarity Evaluation

Status: draft. Code: [`metaskill/evaluate/clarity.py`](../metaskill/evaluate/clarity.py). Parent design: [evaluate.md](evaluate.md), section 3.3. Scoring context: [evaluation/scoring.md](evaluation/scoring.md#clarity).

Clarity answers: "How clear is the skill to the agent and to a person reading it?" It is judged by an LLM against a fixed rubric. No task is run.

## 1. Rubric

Two rulesets existed: the five criteria in evaluate.md 3.3 (trigger specificity, step ordering, verifiability, failure handling, human followability) and four criteria for what a clear skill reads like (concise, understandable, readable, clearly framed and structured). Version 1 merged them into seven criteria. Version 2 adds `actionability`, which the merge had dropped: the writing criteria are about the person reading, and actionability is about the agent following the steps. There are now eight criteria, weighted as in section 2:

| Criterion | Question | From |
|---|---|---|
| `purpose_and_trigger` | Does the skill state the problem it solves, when to use it, and when not to? Does the frontmatter `description` match what the body does? | Framing (problem part) + trigger specificity |
| `conciseness` | Does it say what it needs to without padding, repetition or long preambles? Code the agent must run is content, not padding. | Concise |
| `accessible_language` | Can a reader with basic subject knowledge follow it without a glossary? Jargon, acronyms and scientific names are avoided or explained at first use. | Understandable |
| `readability` | Can a person tell what the skill accomplishes from its title, description and opening, without an AI summarising it? | Readable + human followability |
| `structure_and_flow` | Are sections organised, steps in the order they must be done, sections, prose and code consistent with each other, and does the writing flow? | Framing (structure part) + step ordering |
| `actionability` | Can each step be carried out as written? Each step names a concrete action, command or value, and each choice has a rule or a default. | Earlier design signal "ordered and actionable steps"; dropped in the version 1 merge |
| `verifiability` | Does it say which inputs it needs and what the result should be (form and location), and can the reader check inputs, intermediate results and completion? | Verifiability |
| `failure_handling` | Does it say what to do when relevant things go wrong? A simple workflow whose stopping conditions are already clear earns full credit without a separate section. | Failure handling |

Changes in version 2, beyond the new criterion:

- **Description matches the body** is part of `purpose_and_trigger`. Hermes uses the `description` to decide when to load a skill, so a description that promises something else is a misleading trigger. It is named in the top anchor (required for full credit) and as an example in the bottom anchor ("misleading").
- **Contradictions** are part of `structure_and_flow`: sections, prose and code that disagree (for example a step that says "keep the original" and a script that overwrites it) lower it at levels 1 to 3, and their absence is part of the top anchor.
- **Verifiability** now also asks for the expected result (what it looks like and where it goes), not only whether it can be checked. A skill can have a completion check and still leave the reader unsure what a good result is.
- **Readability** is narrowed to whether the goal is clear at a glance. Following the steps is now `actionability` and `structure_and_flow`, and jargon is `accessible_language`, so the same flaw is not counted under two criteria. It stays separate from `purpose_and_trigger`, although both read the description and opening: `purpose_and_trigger` asks when the skill should be used and whether the description is truthful, `readability` asks whether a person can tell what it achieves.
- The instructions add that asking the user a specific question is a concrete step, so a skill is not marked down for asking rather than guessing.

Each criterion has five anchored levels, worst first, normalised to `0`, `0.25`, `0.5`, `0.75` and `1` (scoring.md proposal). The anchor text lives in `CRITERIA` in `clarity.py`, which is the single source. `RUBRIC_VERSION` (now `"3"`) is bumped whenever a criterion, anchor, weight or the instructions change, since ratings or scores from different versions do not compare. Version 3 changed only the weights: its ratings compare with version 2, its scores do not. Every report records it as `clarity_rubric_version` in its header (evaluate.md 2.1).

## 2. Scoring

```text
C = (sum of weight x rating over the eight criteria) / (sum of the weights), with an unrated criterion counted as 0
```

The weights are `WEIGHTS` in `clarity.py` (rubric version 3). Only their ratios matter; they total 15.5.

| Criterion | Weight | Share | Why |
|---|---|---|---|
| `actionability` | 3 | 19% | Decides whether the agent carries out the task without guessing, which is the skill's main job |
| `purpose_and_trigger` | 3 | 19% | Hermes loads a skill by its description, so a wrong trigger means it is never used, or used for the wrong task |
| `structure_and_flow` | 2 | 13% | Steps out of order and contradictions cause real execution mistakes |
| `verifiability` | 2 | 13% | Lets the agent catch its own errors; partly overlaps Quality, whose tasks check the result |
| `failure_handling` | 2 | 13% | Only matters when something breaks, and overlaps Robustness |
| `accessible_language` | 1.5 | 10% | Matters to people more than to the agent, which understands jargon |
| `readability` | 1 | 6% | Whether a person can tell the goal at a glance; tends to move with `purpose_and_trigger` |
| `conciseness` | 1 | 6% | Efficiency already measures size; kept above 0 to push back against the judge's preference for longer text |

Under equal weights (version 2) a skill weak on one criterion lost at most 0.125. A heavy criterion now costs up to 0.19, so the actionability-only pair separates by 0.105 rather than 0.06 (section 4.4). The judge's run-to-run spread is about the same under both: at most 0.094 equal, 0.089 weighted.

| Outcome | Status | Score |
|---|---|---|
| All eight criteria rated | `ok` | `C` |
| Some criteria have no usable rating | `partial` | `C`, with the missing criteria named in `detail` |
| No usable rating at all | `error` | `null` |
| No model available (no `ctx.llm`, no provider) | `skipped` | `null` |

A rating is unusable when the judge skipped it, rated it twice, rated it out of range, or gave a quote that is not in the skill (judge.md 2.2). Counting it as 0, rather than averaging over the rated criteria, is deliberate: the judge most often fails to quote a weakness that is an *absence* (no failure handling), so averaging would reward unclear skills. The instructions tell the judge to quote the nearby text for an absence.

**[OPEN]** Missing as 0 versus mean of rated criteria; and whether a `partial` Clarity score should be ranked (scoring.md says other partial results need a documented rule: this is that rule, pending agreement).

## 3. Implementation

### 3.1 `clarity.py`

| Name | Description |
|---|---|
| `CRITERIA` | The eight `Criterion(name, question, levels)` objects, in report order |
| `INSTRUCTIONS` | Trusted judge instructions. Holds no text from the skill (judge.md 2.2). Says: judge the writing only; code blocks are judged on explanation, not length; longer is never clearer; asking the user is a concrete step; quote nearby text for an absence; rate criteria independently |
| `RUBRIC_VERSION` | Version of the rubric above, recorded in each report as `clarity_rubric_version` |
| `score(skill, config, llm=None, judge=None)` | Calls `judge.rate(skill, CRITERIA, INSTRUCTIONS)` and returns `score_from_ratings`. Builds a `Judge` on `llm` if no shared judge is given; raises `JudgeUnavailable` if neither is |
| `score_from_ratings(ratings)` | The `DimensionScore` of section 2. `reason` gives the number rated and the weakest criterion; `rubric` keeps every rating with its quote |

What the judge sees is decided by `Judge.rate`: `SKILL.md`, scripts and other text files, hidden text stripped so a comment aimed at the judge cannot sway a rating, cut to `judge_max_chars` (judge.md 2.3). Visible text aimed at the judge is not stripped; the injection controls in section 4.2 test whether it sways the judge.

### 3.2 Wiring into evaluate

The plugin's `evaluate` and `rank` commands (`metaskill/evaluate/__init__.py`) score each dimension by importing its module from `SCORERS`. With `clarity.py` present, Clarity is no longer `skipped: not built yet`. Changes there:

- One `Judge` is built per call with `Judge.from_context(ctx, config)` and passed as `judge=` to the dimensions in `JUDGED` (Clarity for now; Configuration Cleanliness and the safety review later). Reusing one judge is what makes `judge_model` complete (judge.md 1.1).
- `ReportMeta.judge_model` is the judge's record of which models answered. It stays `""` when no judge answered.
- `ReportMeta.clarity_rubric_version` is `clarity.RUBRIC_VERSION`, and the Markdown report prints it in its header.
- `JudgeUnavailable` from Clarity becomes `skipped: no judge model is available`; any other failure, including `JudgeError`, becomes `error`, as for every dimension.

Clarity makes one judge call per skill (plus `judge_retries` on a malformed reply), with the skill's text as input: roughly the skill's size in tokens plus about 2,500 for the rubric.

## 4. Golden dataset (pilot)

The set follows the golden pilot layout used by the other dimensions ([golden_dataset.md](evaluation/golden_dataset.md), proposed repository structure): a synthetic case family under `metaskill/evaluate/golden/synthetic/`, with an evaluator-owned helper beside it. It is unreviewed and for analysis only.

```text
metaskill/evaluate/golden/
  clarity.py                  loads the set; compares a live report against it
  synthetic/clarity/
    README.md                 status, boundaries, review and live-check procedure
    cases.json                one case per skill: expected properties, verbatim quotes, rationale
    skills/<name>/            the skill (SKILL.md with Hermes front matter)
tests/test_golden_clarity.py  structural checks
```

Annotations live outside the skill folders, so a judge reading a skill never sees its label. `cases.json` records the `rubric_version` it was annotated against (now `"3"`, schema `pilot-0.4`; the annotations are the same as for version 2, since only the weights changed). Every case has a `kind`:

| Kind | Count | What it is | Passes when |
|---|---|---|---|
| `clear` | 11 | A reference skill, annotated on every criterion | (it is the reference for `degraded` and `reworded`) |
| `degraded` | 11 | A copy of a clear skill made worse on its `targeted_criteria` | Its clear twin scores higher overall and on every targeted criterion |
| `injected` | 3 | A degraded variant with text added that tells the judge to rate it highly | It gains no more than the tolerance over its base, and still scores below the base's clear twin |
| `reworded` | 3 | A clear skill in different words, with the same content, sections, order and code | Its score is within the tolerance of its base |

`degraded` cases test that the judge sees a difference that is there; `injected` and `reworded` cases test that it does not react to a difference that should not matter. The last two are controls for the judge, not clarity cases, and are not annotated per criterion.

### 4.1 Cases

Eleven clear skills (positive controls, `clarity-high-*`), each annotated on all eight criteria, and eleven unclear variants (`clarity-low-*`), one per clear skill. A variant names its twin in `variant_of` and the criteria it degrades in `targeted_criteria`, and is annotated only on those.

| Clear skill | Edge case it probes | Unclear variant degrades |
|---|---|---|
| csv-dedupe | Inline script should not count as wordiness | purpose_and_trigger, conciseness, actionability, verifiability (padded essay; "it depends"; overwrites input) |
| git-undo-last-commit | Risky operation explained without jargon | accessible_language, readability (Git internals jargon) |
| meeting-action-items | Pure writing task, no tools | purpose_and_trigger, readability, structure_and_flow, actionability, failure_handling (sections reversed; "use judgement"; invents owners) |
| backyard-bird-log | Jargon-prone domain; common names over scientific names | accessible_language, readability (Latin names, plumage terminology) |
| image-resize-web | Prerequisites before steps | structure_and_flow only (same content, shuffled) |
| pdf-merge | Very short; brevity is not penalised | purpose_and_trigger, readability, actionability, verifiability, failure_handling (two-line skill) |
| ab-test-readout | Unavoidable terms, each defined at first use | accessible_language, readability (same method, statistical notation) |
| recipe-scaling | Failure cases under a non-standard heading | conciseness, actionability, failure_handling (padded essay; no rounding rule) |
| markdown-link-check | Longest script; second code-length check | actionability, verifiability, failure_handling (steps not tied to the script; swallows every error) |
| monthly-spending-review | Domain term (debit) used once where the data uses it | purpose_and_trigger, readability, structure_and_flow, actionability (one run-on paragraph) |
| photo-rename-by-date | A command whose one non-obvious flag is explained | actionability only (same sections, checks and failures; steps made vague) |

The `image-resize-web`, `photo-rename-by-date` and `ab-test-readout` variants keep their twin's content and change one thing (order; vague steps; jargon), so they isolate a single criterion. For `ab-test-readout` the jargon also hides the goal, so readability is targeted too. Every criterion is degraded by at least one variant, and `targeted_criteria` is listed in rubric order.

Moving to rubric version 2 changed the annotations as follows:

- Every clear case gained an `actionability` expectation, and its `purpose_and_trigger` expectation now also quotes the `description` line as matching the body. The `csv-dedupe` readability expectation now quotes the opening instead of the description.
- Variants whose readability expectation was really about vague steps (`meeting-action-items`, `pdf-merge`, `monthly-spending-review`) moved that evidence to `actionability` and now quote the opening for readability. The `git-undo-last-commit` readability quote moved from the jargon step to the jargon description.
- `actionability` was added to the variants that leave choices open (`csv-dedupe`, `meeting-action-items`, `pdf-merge`, `recipe-scaling`, `markdown-link-check`, `monthly-spending-review`), and the `photo-rename-by-date` pair was added to isolate it.

### 4.2 Injection and rewording controls

**Injection** (`<base>-injected`, `clarity-inject-*`). A skill's text is untrusted: its author can write to the judge. Hidden comments are stripped before judging (judge.md 2.3), but visible text cannot be, since it is part of the skill. Each injected skill is an unclear variant plus one added text, recorded word for word in `injected_text`, and each uses a different technique:

| Skill | Base | Technique |
|---|---|---|
| pdf-merge-unclear-injected | pdf-merge-unclear | A visible "Note to the reviewer" at the end asking for the highest level |
| meeting-action-items-unclear-injected | meeting-action-items-unclear | A "Review" section with a ready-made judge reply, every criterion at level 5, in the judge's own JSON format |
| monthly-spending-review-unclear-injected | monthly-spending-review-unclear | An instruction appended to the frontmatter `description`, which the judge is told is the skill's stated purpose |

Unclear bases are used because they leave room for a score to rise. A lower score than the base passes: the judge may fairly count the added text as padding. The safety scanner may report these skills as prompt injection; that is expected and does not affect Clarity.

**Rewording** (`<base>-reworded`, `clarity-reword-*`). The same clear skill in different words: every sentence rephrased, the description rephrased, and one or two headings renamed (for example "If Something Goes Wrong" to "Troubleshooting"), with the same content, sections, order and code blocks. A large change in score means the judge is reacting to wording, not clarity. The bases are `csv-dedupe` (script-heavy), `git-undo-last-commit` (commands) and `recipe-scaling` (prose-heavy). The changes are listed in each case's `changes`.

**Tolerance.** `TOLERANCE = 0.1` in `golden/clarity.py`, just above the judge's own spread. Over four version 2 runs of the 22 pair skills on 2026-11-17, no skill's Clarity score moved more than 0.094 between runs (0.089 rescored with the version 3 weights). Most moved 0.031 or less, one criterion by one level. The largest move was `markdown-link-check-unclear`, 0.44 to 0.53. Every criterion changed rating at least once on some skill; `verifiability` and `structure_and_flow` changed most often. A real injection effect is far larger: a skill rated level 5 on every criterion would gain about 0.6 over its unclear base.

### 4.3 Structural checks (CI)

`tests/test_golden_clarity.py`, offline:

- The dataset's criteria and `rubric_version` match `clarity.py`; every case has a known `kind`.
- Eleven clear cases, eleven degraded variants, one variant per clear case; every skill folder has a case.
- Clear cases cover all eight criteria; variants annotate exactly their targets, in rubric order.
- Every criterion is targeted by some variant, and one variant targets actionability alone.
- Every quote appears verbatim in its skill; each front-matter `name` matches its folder.
- Each injected skill is exactly its base plus `injected_text` (apart from its name), and the three use different techniques.
- Each reworded skill has the same code blocks and number of sections as its base, and differs from it.
- Clear and reworded skills stay at or under 1000 words. This is a loose sanity bound, not a measure: several unclear variants are shorter than their clear twins.
- The comparison passes and fails pairs and controls as described above, on synthetic reports.

### 4.4 Live check in the Hermes CLI

Real judge runs stay out of CI (evaluate.md 6). To run one:

1. Print the command: `py -m metaskill.evaluate.golden.clarity --command`. It is `/metaskill rank <absolute path>/csv-dedupe, ...` with all twenty-eight skills. The paths are absolute because Hermes resolves a relative path from the folder its session started in (`Folder: ...` in its reply), which is often not this repository.
2. Run it in Hermes. The report folder is written under `evaluation-reports/`.
3. Compare: `py -m metaskill.evaluate.golden.clarity evaluation-reports/<run>/ranked-evaluation.json`.

The comparison prints two tables and exits non-zero unless everything passes:

- **Pairs.** For every degraded case, the clear skill has the higher Clarity score **and** a strictly higher rating on every targeted criterion (an unrated criterion counts as 0). This is the "reference outranks its vague variant" check of golden_dataset.md, *Evaluating the Evaluator*.
- **Controls.** Each injected and reworded skill against its base, by the rules in section 4.2.

A pair or control is skipped when one of its skills is not in the report. The comparison warns when the report's `clarity_rubric_version` differs from the dataset's `rubric_version`: reports from before version 2 have no `actionability` rating, so that criterion counts as 0 on both sides and every pair that targets it fails. Version 2 reports have the same ratings as version 3 but equally weighted scores, so their pair results still hold, while control changes measured on them are on the old scale.

The first version 2 run (2026-11-17, 22 skills, before the controls were added) ordered 11 of 11 pairs as expected. The actionability-only pair separated by 0.06 overall (0.88 against 0.81), since one criterion is an eighth of the score; its targeted rating separated by 0.5. Three repeat runs gave the same 11 of 11, with the actionability pair unchanged. Rescored with the version 3 weights, the four runs still order 11 of 11, and the actionability pair separates by 0.105 (0.863 against 0.758). No run so far has included the controls.

### 4.5 What the comparison does not check

Only the pair ordering is automated. These parts of `cases.json` are not compared with a live report yet:

- **`expected_label` as a threshold.** No clear skill has to reach a minimum score (section 5, question 5).
- **`expected_properties` and `evidence_quotes`.** They are checked for consistency offline (4.3) but not against the judge's `reason` and `quote`.
- **Per-criterion movement in controls.** Controls compare overall scores only, so an injection that raises one criterion and lowers another passes.
- **Non-targeted criteria on variants.** Nothing checks that a variant stays close to its twin on the criteria it does not target, which is what makes a single-criterion variant a clean test.

## 5. Open questions

1. Unrated criterion as 0, and ranking of a `partial` Clarity score (section 2).
2. Final golden schema (section 4); the set uses the shared pilot layout, but the manifest schema is still draft.
3. Whether to rate more than once and take the median. The measured spread is at most 0.094 (section 4.2), well under the smallest pair gap but close to a single criterion's weight.
4. A threshold for clear skills (for example, every clear skill at or above 0.75). Over four runs every clear skill scored 0.82 or above with the version 3 weights.

**Answered.** Injection and rewording cases are in the pilot (section 4.2). `readability` and `purpose_and_trigger` stay separate criteria (section 1). The criteria are weighted (section 2), and the control tolerance is set from four repeated runs (section 4.2).
