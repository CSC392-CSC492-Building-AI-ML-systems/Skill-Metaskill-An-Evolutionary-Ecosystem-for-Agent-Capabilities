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
| `rate(skill, criteria, instructions="")` | `clarity.py` | `list[RubricRating]`, in the order of `criteria` |
| `review(skill, categories, *, dimension, source="llm-review", instructions="")` | `safety/review.py`, `config_cleanliness.py` | `list[Finding]` |
| `check(task, output, checklist)` / `judge(task, check, run)` | `runner.py` | `Verdict` |
| `ask(instructions, material, schema, purpose)` | the methods above | The parsed JSON object |
| `judge_model` (property) | report writer | `"provider/model"` of every reply, comma separated |

If `judge_model` lists more than one model, Hermes fell back to another model during the run, and the scores did not all come from one judge. Record this in `ReportMeta.judge_model`.

**`rate`** takes `Criterion(name, question, levels)` objects, where `levels` are the anchored levels from worst to best (at least two). The judge picks a level number, and the result is normalised to `[0, 1]`: the worst level is `0.0` and the best is `1.0`. Criteria that the judge skipped, rated twice, rated out of range or supported with an unverifiable quote are left out. The caller decides how a missing criterion scores.

**`review`** takes `categories` as `{name: meaning}`. Findings with an unknown category or severity, or a quote not found in the skill, are dropped. Kept findings get `location` (`path:line`) from the quote, and `rule = category`.

### 2.3 Evidence rules

Every rating, finding and checklist item must quote its supporting text exactly, as one contiguous span. The system prompt (`SYSTEM_PROMPT`) tells the judge this. The code then checks every quote:

- `skill_sources(skill)` is the material the judge sees: `SKILL.md` first, then every bundled file that decodes as UTF-8. Binary files are left out.
- `locate(sources, quote)` returns the `path:line` of the first exact match, or `None`. Line endings are normalised (`\r\n` → `\n`), and whitespace around the quote is ignored.
- Anything whose quote is not found is dropped. This is the main guard against hallucinated evidence (evaluate.md 3.3, evaluate-safety.md 3.3).

## 3. Errors

| Exception | Meaning | What the caller does |
|---|---|---|
| `JudgeUnavailable` | The judge cannot run. Causes: no `ctx.llm`, no provider or credentials (Hermes' `AuxiliaryClientUnavailable`), a provider/model override that Hermes refused, or `make_judge("llm")` without `ctx` | Mark the dimension `skipped` (score `null`) |
| `JudgeError` | The judge ran, but its answer could not be used (schema mismatch, non-JSON reply, request failure), or a deterministic check is malformed | Mark the dimension `error` (score `null`) |
| `ValueError` | Programming error, e.g. a check given to the wrong judge type, or an empty checklist | Fix the caller |

`JudgeUnavailable` from a missing provider only shows up once a call is made. Catch it around `rate`, `review`, `check` and `judge`, not only around construction.

## 4. Testing

The tests in [`tests/test_judge.py`](../../tests/test_judge.py) use `FakeLlm`, which returns canned replies and records each call's arguments. No real model is called, so CI (`pr-checks.yml`) stays offline. Real LLM runs are kept out of CI (evaluate.md 6).
