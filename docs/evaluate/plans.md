# Evaluation plans

Code: [`plans.py`](../../metaskill/evaluate/plans.py). Quality, Robustness and Efficiency need a
task file (evaluate.md 4.3). A plan is a task file that a model drafts **once**, a person reviews
and approves, and every later run reuses unchanged. Tasks are still never generated while a run
is made.

```
/metaskill configure meeting-action-items, document-to-action-items [--name <label>]
/metaskill approve plan-1a2b3c
/metaskill rank plan-1a2b3c          # evaluate plan-1a2b3c for a plan with one skill
/metaskill plans
```

A `/metaskill` command cannot stop to ask a question, so the review is a step of its own between
`configure` and `approve`. `evaluate` and `rank` with skill names and `--tasks` work as before.

## 1. What a plan holds

`metaskill/plans/<id>/` under the Hermes home of the profile, so outside every skill:

| File | Content |
|---|---|
| `plan.json` | skills (name, path, fingerprint), status, label, which model drafted it, dates |
| `tasks.json` | an ordinary task file |
| `fixtures/<task_id>/` | the tasks' input files |
| `examples.json` | for each task, files that make its checks pass; used only to validate, never given to an agent |
| `config.json` | an ordinary evaluation config; `runs_per_task` is 2 in a new plan |

Edit any of them before approving.

## 2. Drafting

`configure` gives the judge model each skill's **name and description** (the start of its text
when it has no description), not its steps: tasks written around one skill's steps would favour
that skill. If the skills do clearly unrelated jobs, no plan is made; skills for the same job are compared
however much their descriptions differ in detail or quality. It asks for up to 3 tasks, each
with input files, `exit_code` check commands and an example answer. The reply is untrusted: names
must be plain file names, sizes and counts are capped (the model is told the limits), a check may
span several lines but holds no other control characters, and anything else rejects the draft with
the reason.

## 3. Validation

Each task is tried in the sandbox: its checks must **fail on the untouched workspace** and **all
pass once the example files are added**. A draft that fails is asked for once more, with the
reason; if it fails again nothing is saved. `approve` validates again, since the files may have
been edited. A plan's checks must be `exit_code` commands, so that they can be validated.

Validation shows a check can tell the example from nothing done. It does not show the check is
right or fair: that is what the review is for.

## 4. Running

| State | `evaluate` / `rank` |
|---|---|
| Draft | refused, with the `approve` command |
| A plan file changed after approval | refused until approved again |
| A skill changed after approval | runs the same tasks, with a warning |
| A skill is gone | refused |
| Options such as `--tasks` or `--config` with a plan | refused: the plan brings its own |

The report records the plan ID. Tasks are never regenerated; run `configure` again for new ones.

## 5. Limits

- Approval records that someone ran `approve`. It is a command only a user types: the `metaskill`
  tool has no approve operation. It is not a security boundary against an agent that can edit
  files under the Hermes home.
- Drafting costs one or two model calls, and validation needs Docker.
