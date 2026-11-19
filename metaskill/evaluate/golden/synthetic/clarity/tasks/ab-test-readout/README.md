# Tasks: ab-test-readout

A task file ([evaluate.md §4.3](../../../../../../../docs/evaluate.md)) for the
`ab-test-readout` skill and its `ab-test-readout-unclear` variant, so both can run the same
tasks and be ranked against each other and against no skill (the baseline). It is kept
outside both skill folders: evaluate refuses a task file inside a skill it evaluates,
because the agent would see the checks.

```
/metaskill rank <clarity>/skills/ab-test-readout, <clarity>/skills/ab-test-readout-unclear --tasks <clarity>/tasks/ab-test-readout/tasks.json
```

Use absolute paths: a relative `--tasks` path is resolved against the Hermes session's
folder. Each task runs `runs_per_task` times (default 3) with the skill and as many times
without it, for each skill ranked, so this file costs 5 tasks × 3 runs × 2 variants × 2
skills = 60 agent sessions, plus the LLM judge's calls.

## Tasks

Every task uses the same prompt: read `experiment.csv`, write `readout.json` with
`winner` (`"A"`, `"B"` or `"none"`), `change_points` (B's rate minus A's, in percentage
points) and `p_value` (or `null`), then explain the result.

| Task | A (conv./visitors) | B | Expected | What it tests |
|---|---|---|---|---|
| `b-wins` | 400 / 5000 | 460 / 5000 | B, +1.20, p ≈ 0.032 | The skill's own example |
| `inconclusive` | 100 / 2000 | 115 / 2000 | none, +0.75, p ≈ 0.29 | Not calling a winner on noise |
| `a-wins` | 640 / 8000 | 560 / 8000 | A, −1.00, p ≈ 0.016 | Direction: the skill's wording only covers "B is higher" |
| `too-small` | 5 / 60 | 9 / 55 | none, `p_value` null | Under 10 conversions: the skill says not to report a p-value |
| `bad-data` | 400 / 5000 | 320 / 300 | none, `p_value` null | Conversions above visitors: step 1's check |

The last two follow guidance that only the clear skill gives ("If Something Goes Wrong";
the unclear one states the normal-approximation condition in notation and has no
input check), so they are where the two variants should differ most.

## Checks

- **`exit_code`** (deterministic, decides the answer): an inline Python command in
  `tasks.json` that reads `readout.json` and asserts the expected values. `change_points`
  must be within 0.05; `p_value` within 15% (at least ±0.005), so other valid tests
  (continuity-corrected chi-square, Fisher's exact) still pass.
- **`llm`** (checklist): whether the explanation says in plain words if the difference
  could be luck, gives the size of the change, and names the problem in the last two
  tasks. It needs a judge model; without one these runs cannot be judged and are runner
  errors, not failures.

The expected values come from the skill's own formula (pooled two-proportion z-test).
