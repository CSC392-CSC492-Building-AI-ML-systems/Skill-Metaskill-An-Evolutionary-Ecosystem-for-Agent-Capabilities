# Evaluation plans

Code: [`plans.py`](../../metaskill/evaluate/plans.py). Quality, Robustness and Efficiency need a
task file (evaluate.md 4.3). A plan is a task file that a model drafts **once**, a person reviews
and approves, and every later run reuses unchanged. Tasks are still never generated while a run
is made.

```
/metaskill configure meeting-action-items, document-to-action-items [--name <name>] [--note "<text>"]
/metaskill approve plan-1a2b3c       # or the name
/metaskill rank plan-1a2b3c          # evaluate <plan> for a plan with one skill
/metaskill plans
```

A `/metaskill` command cannot stop to ask a question, so the review is a step of its own between
`configure` and `approve`. `evaluate` and `rank` with skill names and `--tasks` work as before.

**Names.** `--name demo` lets `approve`, `rank` and `evaluate` take `demo` in place of the ID. A
name is one word of up to 40 letters, digits, `-` and `_`, does not start with `plan-`, and is
unique among plans, in any case. If a name is also a skill's, `rank` and `evaluate` run nothing
and say how to give either one. The ID always works, and reports record the ID.

A plan given without an operation (`/metaskill demo`) runs nothing: the reply says which command
to type. It is not sent to the agent, which cannot run a plan.

## 1. What a plan holds

`metaskill/plans/<id>/` under the Hermes home of the profile, so outside every skill:

| File | Content |
|---|---|
| `plan.json` | skills (name, path, fingerprint), status, name (`label`), which model drafted it, dates |
| `tasks.json` | an ordinary task file |
| `fixtures/<task_id>/` | the tasks' input files |
| `examples.json` | for each task, files that make its checks pass; used only to validate, never given to an agent |
| `config.json` | an ordinary evaluation config; `runs_per_task` is 2 in a new plan |

Edit any of them before approving.

## 2. Drafting

`configure` gives the judge model each skill's name, description and the start of its `SKILL.md`
(4,000 characters, hidden text removed). It is asked to test what the skills teach, such as the
result's format or where settings come from, **without saying it in the task prompt**, so that
an agent without a skill has to guess and the runs can show whether a skill helps. Where the
skills differ, it is asked to test what serves any user of the job, never what only one skill
says.

`--note` (up to 600 characters) says in your own words what the tasks should test, for example
`--note "The company comes from config.json"`. The model is told to follow it over those rules.
The note is shown in the review and kept in `plan.json`.

If the skills do clearly unrelated jobs, no plan is made; skills for the same job are compared
however much their descriptions differ in detail or quality. It asks for up to 3 tasks, each
with input files, `exit_code` check commands and an example answer. The reply is untrusted: names
must be plain file names that every system can write (not `NUL` or one ending in a dot), sizes and counts are capped (the model is told the limits), a check may
span several lines but holds no other control characters, and anything else rejects the draft with
the reason.

**These are requests to a model, not guarantees.** A draft may still give away the answer, test
too little, or favour a skill whose instructions it follows closely. Read every prompt and check
before approving. A run in which skills show no lift suggests the tasks are too easy.

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
| A skill changed after approval (hidden files, `__pycache__` and `node_modules` do not count) | runs the same tasks, with a warning |
| A skill is gone | refused |
| Options such as `--tasks` or `--config` with a plan | refused: the plan brings its own |
| `--override` (with `--config <file>` if wanted) | runs with the user's settings instead of the plan's `config.json`; the run and the report say so ([settings.md](settings.md) 5) |

A plan runs with its own `config.json`, not the user's saved settings (`/metaskill settings`), so it runs as it was approved. The report records the plan ID. Tasks are never regenerated; run `configure` again for new ones.

## 5. Limits

- Approval records that someone ran `approve`. It is a command only a user types: the `metaskill`
  tool has no approve operation. It is not a security boundary against an agent that can edit
  files under the Hermes home.
- Drafting costs one or two model calls, and validation needs Docker.
