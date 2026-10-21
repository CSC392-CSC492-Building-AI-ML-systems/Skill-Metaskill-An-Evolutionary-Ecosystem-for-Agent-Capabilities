# `judge.py` — Judges

Code: [`metaskill/evaluate/judge.py`](../../metaskill/evaluate/judge.py). Design: [evaluate.md](../evaluate.md), sections 3.3, 4.2 and 6.

This module holds two judges:

- **`Judge`** is the LLM judge. It is the only way the evaluate module talks to a model. `clarity.py`, `safety/review.py` and the LLM part of `config_cleanliness.py` go through it and never call a model directly.
- **`DeterministicJudge`** judges task runs with machine-checkable conditions and never calls a model.

Both implement `TaskJudge`, so `runner.py` can judge a task run without knowing which kind of judge it holds.

## 1. Judging task runs (for `runner.py`)

### 1.1 Picking a judge

```python
from metaskill.evaluate.judge import RunOutput, make_judge
from metaskill.evaluate.models import JudgeType

judge = make_judge(JudgeType.DETERMINISTIC)                # no model, no context needed
judge = make_judge(JudgeType.LLM, ctx=ctx, config=config)  # needs the plugin context

verdict = judge.judge(task, check, RunOutput(text=agent_output, workdir=run_dir))
```

`make_judge(judge_type, *, ctx=None, config=None, timeout=60.0)`:

| Argument | Description |
|---|---|
| `judge_type` | `JudgeType.LLM`, `JudgeType.DETERMINISTIC`, or the same values as strings (`"llm"`, `"deterministic"`) |
| `ctx` | The Hermes plugin context passed to `register(ctx)`. Required for the LLM judge |
| `config` | `EvaluationConfig`; the LLM judge reads `judge_provider` and `judge_model` from it. Defaults to `EvaluationConfig()` |
| `timeout` | Seconds an `exit_code` command may run. Deterministic judge only |

Every `Check` records its own judge type, so the runner can choose the judge per check:

```python
for check in task.checks:
    verdict = make_judge(check.judge, ctx=ctx, config=config).judge(task, check, run)
```

Build the LLM judge once per evaluation rather than once per check. It keeps track of which models answered (`judge_model`, section 2.2), and that record is only complete if the same instance is reused.

### 1.2 The interface

```python
class TaskJudge(Protocol):
    judge_type: ClassVar[JudgeType]
    def judge(self, task: Task, check: Check, run: RunOutput) -> Verdict: ...
```

**`RunOutput`** is what one agent run left behind:

| Field | Description |
|---|---|
| `text` | The agent's final output |
| `workdir` | Folder the run happened in. `file_exists` and `exit_code` targets resolve against it |

**`Verdict`** is the outcome of one check. Its fields map directly onto `TestResult`:

| Field | Description |
|---|---|
| `passed` | Whether the check passed |
| `score` | In `[0, 1]`. Deterministic: `1.0` or `0.0`. LLM: fraction of checklist items met |
| `reason` | Why, in plain words |
| `evidence` | LLM: the verified quotes from the output, one per line. Deterministic: always `None` |

A judge given a check of the other type (for example, an LLM check passed to `DeterministicJudge`) raises `ValueError`.

### 1.3 `DeterministicJudge`

Handles the three `CheckKind`s from evaluate.md 4.2:

| Kind | Uses | Passes when |
|---|---|---|
| `file_exists` | `target`: path relative to `workdir` | The path exists |
| `exit_code` | `target`: shell command, run in `workdir`; `expected`: exit code, default `0` | The command exits with `expected` |
| `output_matches` | `expected`: regular expression | The regex is found anywhere in `run.text` (`re.search`, multiline) |

Behaviour to be aware of:

- A `file_exists` target that resolves outside `workdir` (e.g. `../x`) is refused.
- `exit_code` runs the command through a shell. Tasks are human-written (evaluate.md 4.3), so they are trusted. If tasks ever come from untrusted sources, this needs a sandbox.
- An `exit_code` command that runs past `timeout` counts as a failed check, not an error. If the command fails, the last 200 characters of its stderr are added to `reason`.
- A check that cannot be evaluated as written raises `JudgeError`. Examples: an invalid regex, a non-integer `expected` exit code, or an empty `target`. Such an error means the task is broken, not that the skill failed.

### 1.4 LLM checklist judging

`Judge.judge(task, check, run)` calls `Judge.check(task, run.text, check.checklist)`. The judge receives the task prompt, the numbered checklist and the agent output. For each item it answers met or not met and quotes the output.

- An item counts as met only if the judge says it is **and** its quote is found in the output.
- Items the judge skipped count as unmet.
- `passed` is true only when every item is met. `reason` lists each unmet item with the judge's explanation.

## 2. The LLM judge (`Judge`)

### 2.1 How it reaches the model

The judge uses Hermes' plugin LLM access, `ctx.llm`. It runs on the user's own provider and credentials, and the plugin never handles an API key.

Calls run under the plugin's auxiliary task `JUDGE_TASK = "metaskill_judge"`, which `register(ctx)` in [`__init__.py`](../../__init__.py) registers. The judge model is resolved in this order:

1. `judge_provider` / `judge_model` in `EvaluationConfig`, if set. Hermes only allows these overrides once the user enables `plugins.entries.metaskill.llm.allow_provider_override` / `allow_model_override` in `config.yaml`.
2. Otherwise, the model the user pinned under `auxiliary.metaskill_judge` in `config.yaml`.
3. Otherwise, the user's active model.

Construct it with `Judge.from_context(ctx, config)` (or `make_judge`). In tests, pass a fake object with a `complete_structured` method to `Judge(llm)`. See `FakeLlm` in [`tests/test_judge.py`](../../tests/test_judge.py).

### 2.2 Methods

| Method | Used by | Returns |
|---|---|---|
| `rate(skill, criteria, instructions="", **options)` | `clarity.py` | `list[RubricRating]`, in the order of `criteria` |
| `review(skill, categories, *, dimension, source="llm-review", instructions="", **options)` | `safety/review.py`, `config_cleanliness.py` | `ReviewResult` |
| `check(task, output, checklist, **options)` / `judge(task, check, run)` | `runner.py` | `Verdict` |
| `ask(instructions, material, schema, purpose, *, system_prompt, temperature, max_tokens, timeout)` | the methods above, and callers with their own schema | The parsed JSON object |
| `judge_model` (property) | report writer | `"provider/model"` of every reply, comma separated |
| `usage` / `total_usage` | report writer | `TokenUsage` per purpose (`rate`, `review`, `check`, ...) / summed |

If `judge_model` lists more than one model, Hermes fell back to another model during the run, and the scores did not all come from one judge. Record this in `ReportMeta.judge_model`.

**`ask`'s settings.** `options` on `rate`, `review` and `check` are the same keyword arguments (`CallOptions`), passed on to `ask`:

| Argument | Default | Description |
|---|---|---|
| `system_prompt` | `SYSTEM_PROMPT` | Replaces the system prompt, e.g. for a call whose answers carry no quotes. `MATERIAL_IS_DATA` (2.3) is always added to it |
| `temperature` | `0.0` | `None` leaves it to Hermes |
| `max_tokens` | `None` | Reply length cap; `None` leaves it to Hermes |
| `timeout` | `None` | Seconds; `None` leaves it to Hermes |

What a skill is for (its name and description) is not the judge's concern: the caller puts it in `instructions`.

**`rate`** takes `Criterion(name, question, levels)` objects, where `levels` are the anchored levels from worst to best (at least two). The judge picks a level number, and the result is normalised to `[0, 1]`: the worst level is `0.0` and the best is `1.0`. Criteria that the judge skipped, rated twice, rated out of range or supported with an unverifiable quote are left out. The caller decides how a missing criterion scores.

**`review`** takes `categories` as `{name: meaning}`. The judge answers with `category`, `severity`, `file`, `message` and `quote` per finding. Kept findings get `location` (`path:line`) from the quote, and `rule = category`. It returns a `ReviewResult`:

| Field | Description |
|---|---|
| `findings` | The kept findings |
| `dropped` | `DroppedAnswer(answer, reason)` for every answer not kept: unknown category, unknown severity, quote not found in the skill, or not a JSON object. `answer` is the item as the judge returned it, so the caller can report it |
| `skipped` | Files not sent because the skill was over `max_chars` (2.3) |
| `usage` | `TokenUsage` of this review, retries included |

### 2.3 What the judge sees, and evidence rules

`skill_sources(skill)` is the material for `rate` and `review`:

- `SKILL.md` first, then scripts (files under `scripts/` or with a script extension), then the other bundled files that decode as UTF-8. Binary files are left out.
- Hidden text is stripped first (`strip_hidden`): HTML comments and characters that render as nothing (zero-width, bidi controls, tag characters). A comment is replaced by its line breaks, so line numbers still match the file on disk.
- `fit(sources, max_chars)` cuts it to `EvaluationConfig.judge_max_chars` in that order. A file that does not fit is skipped whole, and later, smaller files may still fit. `SKILL.md` is always sent, cut to the budget if it alone is over it.
- Each file is sent in a `<file path="...">` block with numbered lines (`12| ...`).

Everything in the input is untrusted. `MATERIAL_IS_DATA`, added to every system prompt, tells the judge that the input is material to evaluate and that instructions in it are to be judged, not followed.

Every rating, finding and checklist item must quote its supporting text exactly, as one contiguous span. The default system prompt (`SYSTEM_PROMPT`) tells the judge this. The code then checks every quote:

- `locate(sources, quote, file=None)` returns the `path:line` of the first match, or `None`. Any run of whitespace in the quote matches any run in the text, so a re-spaced or re-wrapped quote still counts. Line numbers the judge copied from the material are ignored. `file`, the path the judge named, is searched first; the other files are still searched after it.
- Anything whose quote is not found is not used. This is the main guard against hallucinated evidence (evaluate.md 3.3, evaluate-safety.md 3.3).

### 2.4 Reply format and retries

The JSON schema is sent in the instructions as guidance, with `json_mode` on. It is not passed to `ctx.llm` as `json_schema`: Hermes validates that strictly and fails the whole call on any mismatch, which made 10 of 25 Cisco scans fail on `gpt-5.4-nano` (evaluate-safety.md, safety/cisco.md).

The judge checks each reply itself (`schema_problems`: type, required fields, enums, items; extra fields are ignored). A reply that does not fit, or that Hermes could not parse as JSON, is asked for again with the problems listed, up to `EvaluationConfig.judge_retries` times (default 1, so at most two calls). After the last try:

- A JSON object is returned even if it still does not fit. The methods above drop what they cannot use (`review` lists it in `dropped`).
- Anything else raises `JudgeError`.

Every call, retries included, counts in `usage`.

## 3. Errors

| Exception | Meaning | What the caller does |
|---|---|---|
| `JudgeUnavailable` | The judge cannot run. Causes: no `ctx.llm`, no provider or credentials (Hermes' `AuxiliaryClientUnavailable`), a provider/model override that Hermes refused, or `make_judge("llm")` without `ctx` | Mark the dimension `skipped` (score `null`) |
| `JudgeError` | The judge ran, but its answer could not be used (no JSON object after every retry, request failure), or a deterministic check is malformed | Mark the dimension `error` (score `null`) |
| `ValueError` | Programming error, e.g. a check given to the wrong judge type, or an empty checklist | Fix the caller |

`JudgeUnavailable` from a missing provider only shows up once a call is made. Catch it around `rate`, `review`, `check` and `judge`, not only around construction.

## 4. Testing

The tests in [`tests/test_judge.py`](../../tests/test_judge.py) use `FakeLlm`, which returns canned replies and records each call's arguments. No real model is called, so CI (`pr-checks.yml`) stays offline. Real LLM runs are kept out of CI (evaluate.md 6).
