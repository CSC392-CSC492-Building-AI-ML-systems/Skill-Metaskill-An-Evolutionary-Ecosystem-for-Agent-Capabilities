# Task file examples

[expense-summary](expense-summary) is a small skill with a task file for it. How to write
your own is in [docs/writing-tasks.md](../../docs/writing-tasks.md).

| Path | What it is |
|---|---|
| `expense-summary/skill/` | The skill being scored |
| `expense-summary/tasks.json` | Three tasks: two use the three deterministic checks, one an `llm` checklist |
| `expense-summary/fixtures/` | The files the tasks start with |

The task file and fixtures sit beside the skill's folder, not in it, so the agent cannot
read the checks.

From the repository's folder, check the file first. This runs nothing:

```text
/metaskill tasks check examples/tasks/expense-summary/tasks.json
```

Then run it. This needs Docker and the sandbox image ([README](../../README.md#install)),
and **makes real model calls**: 18 agent sessions and up to 6 judge calls for the `llm`
check at the default 3 runs per task, besides the judge calls `evaluate` makes anyway.

```text
/metaskill evaluate examples/tasks/expense-summary/skill --tasks examples/tasks/expense-summary/tasks.json
```

The tasks are easy, so a capable model may pass them without the skill too. Quality would
then show little or no lift, which is a result, not a fault in the file.
