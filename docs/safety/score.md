# safety/__init__.py

Status: built and tested (`tests/safety/test_score.py`). The default backends are Cisco and own checks; the LLM review is added at `deep` once `review.py` exists.

`score()` also takes an optional `backends` list, used by tests to pass fake backends.

## Purpose

The entry point for the Safety dimension. The evaluation runner calls one function and gets one `DimensionScore` back. It is the only file that knows about all the backends; everything else in `evaluate` sees safety only through this.

## Interface

```python
def score(skill: Skill, config: EvaluationConfig) -> DimensionScore: ...
def missing_dependencies() -> list[Dependency]: ...
def versions() -> dict[str, str]: ...
```

- **`score`**: runs the safety check on one skill.
- **`missing_dependencies`**: what is not installed, with the install command for each. The tool calls this once before evaluation starts so the agent can ask the user whether to install (section 3.1 of the safety design). Safety itself never prompts or installs anything.
- **`versions`**: `{"scanner_version": "2.2.1", "checks_version": "1"}`, for the report header (`ReportMeta`).

## What `score()` does

1. **Pick backends for the tier.**
   - `static`: Cisco and own checks.
   - `deep`: Cisco, own checks and the LLM review.
2. **Run each backend.** Each returns a `BackendResult` and never raises (see [findings.md](findings.md)). They run one after another; they are independent, so this can be made parallel later.
3. **Collect findings** from every backend that produced any.
4. **Merge** them with `findings.merge()`.
5. **Work out the status** from the backend statuses (table below).
6. **Score**: `score_from_findings(merged)`, unless no backend produced a usable result, in which case the value is `None`.
7. **Return** a `DimensionScore` with value, status, reason, detail and the merged findings.

## Status rules

The core rule: **a score of 1.0 must mean "checked and found nothing", never "nothing was checked".** This is the requirement in `scoring.md` from the golden dataset PR.

A backend is **usable** when its status is `ok` or `llm-unavailable` (it still produced static results).

| Situation | Status | Value |
|---|---|---|
| Every backend for the tier is `ok` | `ok` | From findings |
| At least one usable, at least one not `ok` | `partial` | From the usable backends' findings |
| None usable, and every failure is `not-installed` | `skipped` | `None` |
| None usable, at least one `error` | `error` | `None` |

Examples:

| Tier | Cisco | Own checks | Review | Result |
|---|---|---|---|---|
| static | ok | ok | — | `ok` |
| static | not-installed | ok | — | `partial`: own checks only |
| deep | llm-unavailable | ok | ok | `partial`: Cisco ran static only |
| deep | ok | ok | llm-unavailable (no judge) | `partial` |
| static | error | error | — | `error`, value `None` |

`partial` with a numeric value is ranked and flagged (section 5 of evaluate.md).

## Reason and detail

**`reason`**: one line explaining the score.

- `Worst finding: HIGH destructive-unconfirmed at SKILL.md:42`
- `No findings at static tier`
- `Not scored: no safety backend could run`

**`detail`**: empty when the status is `ok`. Otherwise one line per backend that was not `ok`, taken from its `BackendResult.detail`:

```
cisco: not installed; install with `pip install cisco-ai-skill-scanner==2.2.1`
llm-review: no judge configured
```

Scanner notes, such as files the LLM skipped, are added even when the status is `ok`, because they qualify the result.

## Tier mismatch

If `deep` was requested and Cisco fell back to static, the status is `partial` and `detail` says so. The report header still records the requested tier; the per-skill status shows what actually ran. The ranking flags it.

## Tests

Backends are replaced with fakes that return fixed `BackendResult`s, so these tests need neither the scanner nor a model.

- Every row of the status table.
- No backends usable gives `None`, never 1.0.
- Findings from two backends are merged before scoring.
- `reason` names the worst finding; `detail` lists each failed backend.
- Static tier never runs the review backend.

## Open

- Whether the runner calls `missing_dependencies()` once per evaluation or once per session. Proposed: once per evaluation call, before any skill is scanned.
