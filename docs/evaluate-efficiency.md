# Skill Evaluation: Efficiency — Design

How cheaply does a skill get a task done, compared with not using it? Efficiency is
measured from the task runs ([evaluate.md §3.5](evaluate.md)), never estimated from the
skill text. This note fixes the scoring method and what backs it.

Companion to [safety/severity-scale.md](safety/severity-scale.md): same discipline —
state the method, cite what backs it, own what is policy.

## 1. What is measured

Each task is run **with the skill** and **without it** (baseline), on the same inputs.
Efficiency scores the *ratio* of cost, so a skill that makes the agent costlier than no
skill is penalized and one that makes it cheaper is rewarded. Absolute counts are not
comparable across skills; ratios against a per-task baseline are.

The runner records these per run as [`Metrics`](../metaskill/evaluate/models.py). The
**four portable, skill-attributable signals** the score uses:

| Signal | From `Metrics` | Why |
|---|---|---|
| Input | `reproducible_input` (`input + cache_read + cache_write`) | context cost, cache-agnostic so warm and cold runs score alike |
| Generation | `output_tokens` | the tokens the model produced; hidden reasoning is already inside this count (Hermes `normalize_usage`), so `reasoning_tokens` is a sub-breakdown and is **not** added again |
| LLM calls | `llm_calls` | model round-trips / loop efficiency; independent of token volume |
| Tool calls | `tool_calls` | behavioural cost; independent of tokens |

### Measured but NOT scored (and why)

- **Wall-clock `seconds`** — depends on the machine, network and provider load, so it is
  not reproducible. Kept in the log for diagnostics only.
- **Cache split (`cache_read`/`cache_write`)** — a warm/cold coin-flip of recent request
  timing, not a property of the skill. Folded *into* `reproducible_input` precisely so the
  score is cache-agnostic; never scored on its own.
- **Cost in dollars** — real but volatile (prices change), and provider-specific. It
  belongs in the user-facing cost estimate, where today's price is what you want, not in
  a reproducible ranking.
- **Host auxiliary usage** — title generation, summarization and other host side-tasks
  happen regardless of the skill. The agent aggregates only its own task turns into
  `AgentRun` (see its docstring); auxiliary is excluded from the score.

## 2. How the signals combine

**Unweighted geometric mean of per-signal speedup ratios, squashed to [0, 1].**

For each counted task and each signal:

```
r = (baseline_signal + 1) / (with_skill_signal + 1)   # smoothed speedup; > 1 cheaper
```

The `+ 1` is additive (Laplace) smoothing: it keeps `r` defined when either side is zero
and stays within a hair of `baseline/with_skill` for large counts. Without it, a signal
that is zero on either side would drop out, letting a skill add tool calls against a zero
baseline for free, or get no credit for removing them. The trade-off is that small counts
are pulled toward 1: doubling a signal from 1 to 2 is `(1+1)/(2+1) = 2/3`, not the raw
`1/2`. Collect every `r`
across all counted tasks and signals, take their geometric mean `G`, and map to the score:

```
value = G / (1 + G)        # parity (G = 1) -> 0.5; cheaper -> higher; costlier -> lower
```

### Why geometric mean, unweighted (the backing)

- **Geometric mean of normalized ratios** is the standard way to summarize heterogeneous
  normalized measurements — [SPEC CPU](https://www.spec.org/cpu2017/Docs/overview.html#metrics)
  does exactly this (SPECratios). It is unit-free (each `r` is dimensionless), so a token
  ratio and a tool-call ratio combine without asking "how many tokens is a tool call
  worth," and its summary is consistent regardless of which run is the reference.
- **Unweighted** is the defensible default: weighting incommensurable signals needs a
  justification no external standard provides, and the one principled reason to deviate —
  redundancy between correlated signals — was removed by dropping wall-clock time (which
  was the signal most correlated with tokens). The remaining four are comparatively
  independent, so equal treatment double-counts nothing.
- **`G / (1 + G)`** is a parameter-free map to `[0, 1]` with baseline-parity at `0.5`.
  It owns no tunable constant. *(Policy: the band shape is ours; see open questions.)*

## 3. Success-gating

Cost is only credited when the task **succeeded with the skill**. A skill that bails out
early and fails is cheap but worthless, so it earns no efficiency credit.

- A task is **counted** if **any** of its with-skill runs passed; its cost is then averaged
  over **all** of that task's with-skill runs. Failed runs are often the expensive ones
  (the agent loops until it hits a limit), so including them stops a cheap pass from hiding
  an expensive failure. Success decides whether a task counts; cost reflects what the skill
  actually spent.
- If **no** with-skill run passed on any task, efficiency is `None` / `skipped` — there is
  nothing to measure, and quality/robustness already record the failures.

## 4. Smoothing and missing baselines

- A **measured** count contributes a smoothed ratio (§2), so a zero on either side is a real
  comparison, not a dropped signal.
- A signal that was **not measured** (value `None` — `tool_calls` from a usage file, which
  does not report them) is left out, so it cannot add a fixed "even" ratio that dilutes the
  measured signals toward `0.5`.
- A task counts only if it has a **baseline** to compare against; a passed task with no
  baseline is dropped, and `detail` reports "N of M passed tasks counted".

Known limitation: a baseline that failed *cheaply* (did little, then failed) can make the
skill look costlier than it is. Gating the candidate does not fix the reference side; see
open questions.

## 5. Implementation

- [`metaskill/evaluate/efficiency.py`](../metaskill/evaluate/efficiency.py) — pure logic
  over runner results, no agent run and no model call.
  - `score_from_runs(with_skill, baseline)` holds §2–§4; it is what the runner
    integration calls once task-based dimensions are wired in.
  - `score(skill, config, llm=...)` is the dimension entry point. The runner is **not yet
    wired into `evaluate`** (no task-based dimension runs tasks — evaluate.md §3.5), so in
    the live pipeline it returns `skipped`. The scoring logic is built and tested ahead of
    that integration, as safety's scoring was.
- Signals read from `Metrics` (`reproducible_input`, generation = `output_tokens`), so a
  change to what counts touches one place. A signal that returns `None` (not measured —
  `tool_calls` from a usage file) is left out of the mean, not smoothed as a zero.

## 6. Open questions

1. **Band shape.** `G / (1 + G)` is parameter-free with parity at `0.5`; whether a
   different `[0, 1]` map (e.g. a steeper curve) better separates skills is untested.
2. **Reference side.** A cheaply-failed baseline understates efficiency (§4). Whether to
   gate the baseline, or compare only against passed baselines, is open.
3. **Cross-task aggregation.** One geomean over all (task, signal) ratios treats every
   ratio equally; whether to average per-task scores instead is untested.
4. **Weighting.** Unweighted is the default (§2). If the golden data shows strong residual
   correlation among the four signals, a correlation-de-weighted geomean is the backed
   alternative; the weights would be calibrated, not chosen.
5. **Static size.** `SKILL.md` and bundle size are paid on every load; whether to fold
   this in as a minor secondary signal is open.
