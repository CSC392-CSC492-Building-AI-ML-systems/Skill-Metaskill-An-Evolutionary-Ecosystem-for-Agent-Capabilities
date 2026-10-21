# review.py

Status: built and tested (`tests/safety/test_review.py`). A thin layer over `judge.py` ([evaluate/judge.md](../evaluate/judge.md)): `Judge.review()` sends the skill, checks every quote and drops what it cannot use; this file adds what is specific to safety. Part of the default backends at the `deep` tier.

## Purpose

An LLM pass for what rules cannot detect, mainly **`excess-scope`**: actions or permissions outside what the skill says it is for. A skill described as "summarize a CSV" that also deletes files or posts to a webhook is out of scope, but no pattern can know that without reading the skill's purpose. Runs only in the `deep` tier (section 3.3 of the safety design).

## Interface

```python
class ReviewBackend:
    name = "llm-review"
    def __init__(self, judge: Judge | None): ...  # None outside Hermes
    def available(self) -> bool: ...        # a judge exists; whether a model answers is only known at the first call
    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult: ...
```

`safety.default_backends()` adds it at the `deep` tier only, with a `Judge` built from Hermes's `ctx.llm` and the evaluation config (provider and model overrides, `judge_retries`, `judge_max_chars`). Without `ctx.llm` it is still added, with no judge, so its absence shows as `llm-unavailable` and safety as `partial`. Called at `static`, it returns `ok` with no findings.

## What the model is asked

One `Judge.review()` call per skill. The re-check of skipped warnings (below) is a second, smaller `Judge.ask()` call, made only when the own checks skipped something.

- **The skill's files:** the judge sends them, with paths and line numbers ([judge.md](../evaluate/judge.md)).
- **The four categories** from section 2.1 of the safety design, each with a definition and an example (`CATEGORIES`).
- **Instructions** (`INSTRUCTIONS`): report only what the skill tells the agent to do, not what it warns against, with severity guidance:

  | Severity | When |
  |---|---|
  | CRITICAL | Could destroy a system or home folder, or send credentials or private data to a third party |
  | HIGH | Irreversible data loss, or a secret exposed, in normal use |
  | MEDIUM | Risky but recoverable, or outside the stated purpose without clear harm. `excess-scope` is MEDIUM unless the action is also destructive or sends data out. |
  | LOW | A minor risk |

**The stated purpose stays in the material.** `excess-scope` needs the skill's purpose, its frontmatter `name` and `description`. These are written by the skill's author, so they are untrusted and are never copied into the instructions, which are the trusted part of the prompt. The frontmatter is already part of `SKILL.md`, which the judge sends as material, so the instructions only tell the model where to find it and that it is a claim, not an instruction.

**Why all four categories, not only `excess-scope`:** the rules in `checks.py` miss phrasing they do not know, such as "wipe the build folder" with no command. The review is a second opinion on those. Findings that duplicate a rule's finding at the same location are merged by `findings.merge()`; a review finding whose quote starts on an earlier line than the rule's match is not merged, but the score is unaffected, since only the worst finding counts.

**Checking the answer, size limits, and treating the skill as data** are all done by the judge: retries, unknown categories and severities, quotes not in the skill, locations, hidden text, the size cap and `MATERIAL_IS_DATA` ([judge.md](../evaluate/judge.md) 2.2 to 2.4). `review.py` reports what the judge dropped and skipped in `detail`, e.g. "2 review answer(s) dropped: quote not found in the skill".

## Re-checking skipped warnings

The own checks skip prose matches that look like warnings ([checks.md](checks.md), warning check). The heuristic misreads some sentences, so at the `deep` tier the review re-checks every skipped match and can **restore** any that is a real instruction.

**It can only restore, never remove.** The model only sees matches the static check already skipped, and its only possible effect is to turn one back into a finding:

| Static | Model says | Result |
|---|---|---|
| Skipped as a warning | Real instruction | Restored as a finding, `source: "llm-review"` |
| Skipped as a warning | Genuine warning | Stays skipped, the same as `static` |

So a prompt injection cannot make a skill look safer than the deterministic result. At worst it keeps a skip that `static` would have kept anyway. An injection that restores a finding only lowers the injecting skill's own score.

**When it runs:** during the safety scan, not afterwards, because a restored finding changes the score. One batched call per skill, with all of that skill's skipped matches.

**Input per match:** from `checks.skipped_warnings()`, an opaque ID, the matched command, the sentence and its paragraph, as JSON. Enough context to judge, without sending the whole skill. The location is not sent, so the known-answer items cannot be told apart from real ones; items are shuffled in an order fixed by the batch. At most `MAX_RECHECK_ITEMS` (40) matches go in one call; the rest stay skipped and `detail` says how many. Matches that were also confirmed are not re-checked, since they would not be findings either way.

**Restrictions on the call.** These also apply to the main review call above.

| Restriction | Prevents |
|---|---|
| No tools: a plain API call | The model doing anything: no commands, files or network |
| Fixed answer format: JSON schema, `[{"id", "verdict": "instruction" \| "warning"}]` | Answers we did not ask for |
| Our code validates the answer: known IDs and the two verdicts only; a missing verdict leaves the match skipped | A malformed or manipulated reply having an effect |
| One direction only, enforced in code | Injection making a skill look safer |
| The reply is only read as data, never passed to a shell, `eval` or another prompt | Its output being executed |
| Minimal input: sentence and paragraph, not the whole skill | Room for an injection to work with |
| Hidden text removed first: HTML comments, invisible Unicode. The judge does this for the main call; `review.py` runs `strip_hidden()` on the re-check's sentences and paragraphs | Instructions a human reviewer cannot see |
| One skill per call, no shared conversation | One skill's text affecting another's verdicts |
| Small `max_tokens`, temperature 0, a timeout (`ask()`'s parameters), and `judge_retries` retries | Runaway output and cost |

The re-check goes through `Judge.ask()`, not `review()`, with its own `system_prompt`: its answers carry no quotes, and the default system prompt requires one on every answer.

**Known-answer items.** Each batch also contains two control items with known verdicts: a clear warning and a clear instruction. If the model gets either wrong, the whole batch is discarded and every match stays skipped. This is a direct signal that something in the batch, possibly an injection, confused the model.

**Failure:** if the re-check call fails, or the batch is discarded because a known-answer item was answered wrongly, the matches stay skipped and the backend reports `llm-unavailable`. The main review's findings are kept, since only the re-check is missing. A restored finding keeps the rule, severity and location of the own-check match it came from, with `source: "llm-review"` and a note in its message.

What none of this restricts is the model's judgment: it still reads the skill's text, which can still sway which of the two verdicts it picks. The one-direction rule is what makes that safe.

## Status

| Situation | Status |
|---|---|
| Reviewed, and re-checked if needed | `ok` |
| No judge (no `ctx.llm`), or `JudgeUnavailable` (no provider, or a refused override) | `llm-unavailable`, tier `static` |
| Review done, but the re-check failed or was discarded | `llm-unavailable`, review findings kept |
| `JudgeError` (no JSON object after every retry, or a request failure), or any other error | `error` |

## Cost and privacy

- One review call per skill, plus one batched re-check call when the own checks skipped warnings. Either may be asked again when its reply does not fit the schema, up to `judge_retries` times, so with the default of 1 a skill costs two to four calls at worst. Token use is recorded so the real cost can be measured (open question 4 in the safety design): `ReviewResult.usage` for the review, and `Judge.usage` per purpose for every call.
- The full skill is sent to the configured provider. The tool states which provider receives what before the first call (section 4 of the safety design). This matters most for skills that have not been generalized yet.

## Tests

A fake judge returns fixed answers, so no test calls a real model. Parsing, retries, quote checking, size limits and hidden-text stripping are tested with the judge (`tests/test_judge.py`); `tests/safety/test_review.py` covers what `review.py` adds:

- Static tier does nothing; no judge gives `llm-unavailable`.
- Review findings are returned; categories, dimension and source are passed; the stated purpose is not copied into the instructions.
- Dropped answers and skipped files are reported in `detail`.
- `JudgeUnavailable`, `JudgeError` and other errors become statuses.
- Re-check: an "instruction" verdict restores the match with its rule, severity and location; "warning", missing and unknown IDs leave it skipped; a wrong known-answer item discards the batch; no verdict removes a review finding; the items sent carry only `id`, `command`, `sentence` and `paragraph`; hidden text is stripped; the batch is capped; a failed re-check keeps the review's findings.
- One test runs the real `Judge` against a fake `ctx.llm`: both calls go through the `metaskill_judge` model setting with `MATERIAL_IS_DATA` in the system prompt.
- `default_backends()` adds the review at `deep` only, with no judge when there is no `ctx.llm`.

## Open

- Default severity guidance for `excess-scope`. Proposed: MEDIUM unless the out-of-scope action is also destructive or sends data out.
- Results vary between runs (section 9 of the safety design). Whether to run the review more than once and keep findings that appear in most runs.
- **[OPEN] Whether the review may ever make a result more lenient** than the deterministic layers, by downgrading or clearing a finding (open question 7 in the safety design). The motivating case: Cisco's `DATA_EXFIL_NETWORK_REQUESTS` fires at MEDIUM on any outbound request, because a static rule cannot tell a skill's intended traffic from exfiltration. `arxiv` gets two such findings for fetching papers from arXiv, which is its whole purpose, so its safety score is 0.6, and none of Cisco's scan presets changes this. Judging whether a network call fits the skill's purpose is the same judgment as `excess-scope`, so the review could mark such findings as in scope. This is the risky direction: unlike the skipped-warning re-check, an injection here could make a skill look safer than the rules say. If allowed, it needs: which findings may be touched and by how much, the original kept in the report, CRITICAL findings and commands in code blocks or scripts left untouchable, and the restrictions above. Waits on the validation set.
