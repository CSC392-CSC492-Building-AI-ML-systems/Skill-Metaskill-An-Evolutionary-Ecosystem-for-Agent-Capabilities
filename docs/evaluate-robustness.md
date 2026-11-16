# Skill Evaluation: Robustness — Design

Does the skill behave the same way each time it does the same task? Robustness is measured
from repeated task runs ([evaluate.md §3.2](evaluate.md)), never estimated from the skill
text. It measures consistency, not correctness: a skill that fails every run in the same
way is robust and has poor Quality. This note fixes the scoring method.

Extends the proposal in [evaluation/scoring.md](evaluation/scoring.md#robustness)
(pairwise outcome agreement and score spread) with agreement of outputs and methods.
**[PROPOSED]**: the terms and their equal weights are for team review.

## 1. What is measured

Only **with-skill** runs are scored. Each task is run `n` times (`runs_per_task`, default
3), and every distinct pair of runs `i < j` of a task is compared on four agreements, each
in `[0, 1]`:

| Term | Pair agreement | Criterion |
|---|---|---|
| Outcome | both passed: `1`; both failed: `0.5 + 0.5 × sim(reasons)`; one passed: `0` | Passing consistently; repeating the same bug |
| Score | `1 − abs(score_i − score_j)` | Spread of partial credit (scoring.md) |
| Output | `sim(output_i, output_j)` over lines | Variance in what the skill produces, including code |
| Method | `sim(transcript_i, transcript_j)`, or call-count agreement when no transcripts | Passing by very different routes |

`sim` is a line diff: difflib's ratio over the two texts' lines, with surrounding
whitespace removed and blank lines dropped, so re-indenting or an extra blank line is not a
different result. Two empty texts are identical. At most 2,000 lines of each are compared.

- **Output** is `TestResult.output`, without the ` (full log: …)` suffix the runner adds:
  that path names the run, so it differs every time.
- **Method** compares the two runs' transcripts when the caller passes them (the runner
  keeps transcripts only in the run logs). Otherwise it is the mean over `tool_calls` and
  `llm_calls` of `(min + 1) / (max + 1)`, the same smoothing efficiency uses; a count not
  measured (`None`) is left out. Token counts are not used: they measure volume, which the
  output comparison already covers.
- **Same bugs.** Two failed runs always agree on the outcome, and agree fully only if they
  failed the same way: `reasons` is the run's check reasons, which the runner joins with
  `; `, compared as a sequence. The same failed checks with the same reasons are the same
  flaw, repeated. That is consistent behaviour, though wrong, so it scores higher than
  failing differently each time, and Quality records that it is wrong.
- **Different outcomes.** A pass and a failure are different results however alike their
  text or call counts, so such a pair scores `0` on output and method; only its score term
  can be above `0`.

## 2. How the terms combine

```
O_t, S_t, X_t, M_t = mean over pairs of the four agreements
R_t = (O_t + S_t + X_t + M_t) / 4
R   = mean over tasks of R_t
```

Unweighted, as for efficiency: no criterion has a backed claim to more weight. For binary
scores, scoring.md's examples hold exactly:

| Runs | Outcome | Score | Output | Method | Robustness |
|---|---|---|---|---|---|
| Pass, pass, pass (same output and method) | 1 | 1 | 1 | 1 | 1.000 |
| Fail, fail, fail (same bug) | 1 | 1 | 1 | 1 | 1.000 |
| Pass, fail, fail (failures alike) | 1/3 | 1/3 | 1/3 | 1/3 | 0.333 |

Passing every time, by different code and routes, keeps outcome and score at 1 while
output and method fall, so the skill lands between the two (criterion 3). Failing three
different ways keeps outcome between 0.5 and 1.

The report gives the weakest task in `reason`, and the four means and which method source
was used in `detail`.

## 3. Evidence rules

- **Baseline** runs are not scored. Their plain pass/fail agreement is added to `detail` as
  context (scoring.md).
- **At least two runs** per task are needed. With one run per task, Robustness is `skipped`.
- **A complete run matrix** is required (scoring.md, scoring conventions). If tasks have
  different numbers of with-skill runs, fewer than `runs_per_task` when the caller gives
  it, or two results claim the same task and run, Robustness is `error` with no score:
  a lost run must not quietly leave the denominator.
- No with-skill runs at all is `skipped`.

## 4. Implementation

- [`metaskill/evaluate/robustness.py`](../metaskill/evaluate/robustness.py) — pure logic
  over runner results, no agent run and no model call.
  - `score_from_runs(results, *, runs_per_task=None, transcripts=None)` holds §1–§3.
    `transcripts` maps `(task_id, run)` to that run's transcript.
  - `task_consistency(runs, transcripts=None)` returns one task's four terms
    (`TaskConsistency`), for tests and diagnostics.
  - `score(skill, config, llm=...)` is the dimension entry point. The runner is **not yet
    wired into `evaluate`**, so in the live pipeline it returns `skipped`, as efficiency
    does.
- Tests: [`tests/test_robustness.py`](../tests/test_robustness.py), on recorded results
  only.

## 5. Open questions

1. **Weights.** The four terms are equal. Outcome and score overlap for binary scores
   (scoring.md); output and method overlap when the output is the work itself.
2. **Method without transcripts.** Call counts are a weak proxy: the same counts can hide
   different tools. Whether the runner should pass transcripts, or a list of tool names
   per run, is open.
3. **Long outputs.** A line diff of a long reply is slower and noisier than of a short
   one. Whether to compare only the files the run produced, or a normalised form of the
   output, is untested.
4. **Run count.** Three runs give three pairs; whether that separates skills reliably is to
   be measured on the golden dataset.
