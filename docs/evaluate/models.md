# `models.py` — Evaluation data models

Code: [`metaskill/evaluate/models.py`](../../metaskill/evaluate/models.py). Design: [evaluate.md](../evaluate.md), section 2.1 (report format).

These are the data structures shared by the scoring modules, `runner.py` and `rank.py`. They mirror the report format in evaluate.md 2.1. Models with invariants check them on construction, including scores in `[0, 1]`, `run ≥ 1`, required checks and the status/value rule. Every report model round-trips through `to_dict()` / `from_dict()`.

## 1. Overview

```
Inputs                       Scoring output               Report
------                       --------------               ------
Skill                        DimensionScore  ─┐           Report
EvaluationConfig ─► ReportMeta                ├─► SkillResult ─┘ (meta + skills)
Task ─► Check                TestResult      ─┤
                             RubricRating    ─┤
                             Finding         ─┘
                             Scores, DimensionStatus
```

## 2. Enums

All are `StrEnum`s, so they serialise as their plain string value.

| Enum | Values | Meaning |
|---|---|---|
| `Dimension` | `quality`, `robustness`, `clarity`, `config_cleanliness`, `efficiency`, `safety` | The six scored criteria (evaluate.md 1) |
| `Variant` | `with_skill`, `baseline` | Whether a task run had the skill; needed for lift |
| `JudgeType` | `deterministic`, `llm` | How a task check is judged (evaluate.md 4.2). Selects the judge in `make_judge` ([judge.md](judge.md)) |
| `CheckKind` | `file_exists`, `exit_code`, `output_matches` | The deterministic checks |
| `SafetyTier` | `static`, `deep` | Safety scan depth ([evaluate-safety.md](../evaluate-safety.md)) |
| `Severity` | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO` | Severity of a finding |
| `Status` | `ok`, `partial`, `skipped`, `error` | Whether a dimension ran as configured (section 4) |

`TIMESTAMP_FORMAT = "%Y-%m-%d-T%H-%M-%S"` (e.g. `2026-10-14-T15-30-02`) is used for both the run folder name and `ReportMeta.timestamp`.

## 3. Inputs

### `Skill`

A skill loaded from disk. Every scoring module takes one.

| Field | Description |
|---|---|
| `path` | The skill directory |
| `name` | `name` from the frontmatter, otherwise the directory name |
| `body` | The full contents of `SKILL.md`, frontmatter included, so line numbers match the file |
| `frontmatter` | Parsed YAML frontmatter; `{}` if there is none |
| `files` | Bundled files relative to `path`, sorted. Excludes `SKILL.md`, anything under a dot-prefixed path, `__pycache__` and `node_modules` |

`Skill.load(path)` builds one from a directory. PyYAML is only needed if `SKILL.md` has frontmatter. Frontmatter that is not a mapping raises `ValueError`.

### `EvaluationConfig`

Settings for one `evaluate` or `rank` call.

| Field | Default | Description |
|---|---|---|
| `runs_per_task` | `3` | `n` in evaluate.md 4.4. Must be ≥ 1 |
| `judge_provider` | `""` | Provider override for the LLM judge; empty follows Hermes config ([judge.md](judge.md) 2.1) |
| `judge_model` | `""` | Model override for the LLM judge |
| `judge_retries` | `1` | Times a judge reply that does not fit its schema is asked for again ([judge.md](judge.md) 2.4). Must be ≥ 0 |
| `judge_max_chars` | `100000` | Characters of a skill sent to the judge; larger skills are cut ([judge.md](judge.md) 2.3). Must be ≥ 1 |
| `safety_tier` | `static` | Safety scan depth |
| `weights` | `DEFAULT_WEIGHTS`: safety 4, quality 3, robustness 3, clarity 2, efficiency 2, config cleanliness 1 | Relative weight of each dimension in `overall` (evaluate.md 5.1), divided by their sum. Each must be a number (not text or `true`/`false`, as a config file could give), ≥ 0 and finite, and at least one above 0 (`check_weights`, which raises `ValueError`). A dimension left out weighs 0. `DEFAULT_WEIGHTS` is read-only; each config gets its own copy |
| `output_max_chars` | `2000` | Truncation length for `TestResult.output`; the full output is in the run's log ([runner.md](runner.md) 2.4) |
| `sandbox_disk` | `16m` | Size of each run's folder in the sandbox ([runner.md](runner.md) 2.6); the task's fixtures must fit in it too. A number and `k`, `m` or `g`, nothing else, since it goes into the volume's mount options |
| `output_dir` | `evaluation-reports` | Each run writes `[timestamp]/` here |

- `hash()` returns the first 16 hex characters of a SHA-256 over every setting except `output_dir`, since the output location does not affect scores. The result becomes `ReportMeta.config`.
- `to_meta(timestamp, skills, scanner_version, checks_version)` builds the `ReportMeta` for a run.
- `from_dict` re-validates `weights` after loading them.

### `Check`

How one task outcome is judged (evaluate.md 4.2). A task can have several.

| Field | Used by | Description |
|---|---|---|
| `judge` | both | `JudgeType`; picks the judge |
| `kind` | deterministic | `CheckKind`. Required when `judge` is `deterministic` |
| `target` | deterministic | File path for `file_exists`; shell command for `exit_code`; unused for `output_matches` |
| `expected` | deterministic | Exit code for `exit_code` (as text, default `0`); regex for `output_matches` |
| `checklist` | LLM | Items the output must meet. Required (non-empty) when `judge` is `llm` |

See [judge.md](judge.md), section 1.3, for exactly how each kind is evaluated.

### `Task`

One human-written test task (evaluate.md 4.3), run with and without the skill.

| Field | Description |
|---|---|
| `task_id` | Stable identifier, so results can be compared across reports |
| `description` | One-line summary |
| `prompt` | What the agent is asked to do |
| `checks` | `list[Check]`; must not be empty |
| `fixtures` | Files copied into the run folder before each run |

## 4. Scoring output

### `DimensionScore`

What every scoring module's `score(skill, ...)` returns.

| Field | Description |
|---|---|
| `dimension` | Which dimension |
| `value` | Score in `[0, 1]`, or `None` |
| `status` | `Status`; default `ok` |
| `reason` | Why it scored what it did |
| `detail` | What was missing or went wrong when `status` is not `ok` |
| `rubric` | `RubricRating`s, for Clarity |
| `findings` | `Finding`s, for Safety and Configuration Cleanliness |

Invariant: `value` is `None` **exactly** when `status` is `skipped` or `error`. This keeps a skill from being penalised for a missing key or backend (evaluate.md 2.1). `partial` still has a value. `to_status()` returns the `DimensionStatus` part kept in the report.

### `TestResult`

One task, one variant, one run. A single record feeds Quality, Robustness and Efficiency.

| Field | Description |
|---|---|
| `task_id`, `description` | Copied from the `Task` |
| `variant` | `with_skill` or `baseline` |
| `run` | Run index, `1..n` |
| `judge` | `JudgeType` that judged it |
| `passed`, `score`, `reason`, `evidence` | Copied from the judge's `Verdict` ([judge.md](judge.md) 1.2). `score` must be in `[0, 1]`; `evidence` is `None` unless judged by the LLM |
| `metrics` | `Metrics(tokens, seconds, tool_calls)`; cost of the run, for Efficiency |
| `output` | Agent output, truncated to `output_max_chars`, with the path to the full log |

### `RubricRating`

One Clarity rubric criterion (evaluate.md 3.3), produced by `Judge.rate`.

| Field | Description |
|---|---|
| `criterion` | e.g. `trigger_specificity` |
| `rating` | Anchored level normalised to `[0, 1]` |
| `reason` | Why the judge chose this level |
| `quote` | Verified text from the skill |

### `Finding`

A problem found in a skill's files, for Safety or Configuration Cleanliness.

| Field | Description |
|---|---|
| `dimension` | Dimension it counts towards |
| `category` | Kind of problem; for safety, see [evaluate-safety.md](../evaluate-safety.md) section 2 |
| `severity` | `Severity` |
| `source` | Which checker reported it: `cisco`, `own-checks`, `llm-review` |
| `rule` | Identifier of the check that fired |
| `quote` | The offending text |
| `location` | `path:line`, e.g. `SKILL.md:42` |
| `message` | What is wrong, in plain words |

## 5. Report

### `Scores`

One value per dimension plus `overall`, each in `[0, 1]` or `None` (skipped or failed). `overall` is the weighted mean of the six ([rank.md](rank.md)), and `None` if any dimension of weight above 0 is `None`; such a skill is listed below the ranked ones (evaluate.md 5.2). `rank.make_scores(dimension_scores, config)` builds it from the scoring modules' `DimensionScore`s, with `config.weights`.

### `DimensionStatus`

`status`, `reason`, `detail`: the part of a `DimensionScore` that the report keeps for each dimension.

### `SkillResult`

Everything evaluated for one skill.

| Field | Description |
|---|---|
| `name` | Skill name |
| `scores` | `Scores` |
| `lift` | `pass_rate(with_skill) - pass_rate(baseline)`, in `[-1, 1]` (evaluate.md 4.1). Checked on construction; NaN is refused |
| `dimensions` | `{Dimension: DimensionStatus}` |
| `tests`, `rubric`, `findings` | All `TestResult`s, `RubricRating`s and `Finding`s for the skill |

### `ReportMeta`

The settings a run used, so a reader can tell what produced the scores. It is a record, not a comparison check, since every ranking is fresh (evaluate.md 5).

| Field | Description |
|---|---|
| `timestamp` | In `TIMESTAMP_FORMAT` |
| `skills` | Skills evaluated |
| `config` | `EvaluationConfig.hash()` |
| `runs_per_task` | `n` |
| `judge_model` | Model(s) that judged. Set from `Judge.judge_model` after the run, so a mid-run fallback is visible |
| `safety_tier` | Tier used |
| `scanner_version` | External scanner version; empty if it did not run |
| `checks_version` | Version of our own safety rule set |

### `Report`

`meta: ReportMeta` plus `skills: list[SkillResult]`. These are the contents of `evaluation-reports/[timestamp]/ranked-evaluation.json`.

## 6. Serialisation

- `to_dict()` returns JSON-ready dicts. Enums are `StrEnum`s, so `json.dumps` writes them as plain strings.
- `from_dict()` rebuilds the object. It runs the same validation as construction, so a hand-edited report with an out-of-range score or an inconsistent status fails to load.
- Optional fields fall back to their defaults when missing (e.g. `DimensionScore.status` → `ok`, `TestResult.evidence` → `None`).
