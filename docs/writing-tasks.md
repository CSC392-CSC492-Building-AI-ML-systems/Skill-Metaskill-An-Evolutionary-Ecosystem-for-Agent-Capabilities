# Writing a task file

A task file tells `evaluate` and `rank` what to ask an agent to do and how to tell whether
it did it. Every task is run several times **with** the skill and **without** it, and the
runs are scored for Quality, Robustness and Efficiency. Without a task file those three
are skipped.

A complete example is in [examples/tasks](../examples/tasks).

```text
/metaskill tasks check my-evals/tasks.json
/metaskill evaluate my-skill --tasks my-evals/tasks.json
```

## Layout

Keep the task file and its fixtures in a folder of their own, **outside every skill you
evaluate**. The skill's folder is copied into the sandbox, so a task file inside it would
show the agent the checks, and `evaluate` refuses it.

```text
my-skill/
  SKILL.md
my-evals/
  tasks.json
  fixtures/
    march/expenses.csv
```

## Format

The file is JSON, or YAML (`.yaml` or `.yml`, read with Hermes' YAML loader). It holds a
list of tasks, or an object with a `tasks` list.

```json
{
  "tasks": [
    {
      "task_id": "march",
      "description": "A month with two refunded rows",
      "prompt": "expenses.csv in this folder lists expenses. Rows whose status is \"refunded\" do not count. Write the total of the rest to total.txt, then tell me the total.",
      "fixtures": ["fixtures/march/expenses.csv"],
      "checks": [
        {"judge": "deterministic", "kind": "file_exists", "target": "total.txt"},
        {"judge": "deterministic", "kind": "exit_code", "target": "grep -qx '571.80' total.txt", "expected": "0"},
        {"judge": "deterministic", "kind": "output_matches", "expected": "571\\.80?"}
      ]
    },
    {
      "task_id": "explain",
      "description": "Explaining what was left out",
      "prompt": "expenses.csv in this folder lists expenses. Rows whose status is \"refunded\" do not count. Explain why the total is lower than the sum of every row.",
      "fixtures": ["fixtures/march/expenses.csv"],
      "checks": [
        {"judge": "llm", "checklist": ["Says that the refunded rows were left out of the total"]}
      ]
    }
  ]
}
```

| Task field | Required | Meaning |
|---|---|---|
| `task_id` | yes | A short unique name. Letters, digits, `_`, `.` and `-`, starting with a letter or digit. It names the run logs |
| `description` | yes | What the task tests, for whoever reads the report. The agent does not see it |
| `prompt` | yes | What the agent is asked to do. The only part of the task it is told |
| `checks` | yes | One or more checks. A run passes only if **all** of them pass |
| `fixtures` | no | Files and folders the agent starts with |

| Check field | Used by | Meaning |
|---|---|---|
| `judge` | every check | `deterministic` (no model call) or `llm` |
| `kind` | deterministic | `file_exists`, `exit_code` or `output_matches` |
| `target` | `file_exists`, `exit_code` | The file's path, or the command |
| `expected` | `exit_code`, `output_matches` | The exit code (default `0`), or the regular expression |
| `checklist` | `llm` | The statements that must all be true of the agent's answer |

Any other field is ignored, and `tasks check` warns about it.

## Choosing a check

Use a deterministic check wherever the answer can be tested by a machine: it costs nothing
and gives the same verdict every time. Use an `llm` check for what only a reader can judge.

### `exit_code`

Runs `target` with `sh -c` in the run's folder, after the agent has finished, and compares
the exit code with `expected`. **Use it to test what the files contain.**

```json
{"judge": "deterministic", "kind": "exit_code",
 "target": "python3 -I -c 'import json; assert json.load(open(\"summary.json\"))[\"total\"] == 571.8'"}
```

- It runs in the sandbox: `python:3.12-slim`, no network, no extra packages. It gets 60
  seconds.
- It cannot use files on your machine. Put the test in the command itself.
- The agent could have changed anything in the folder, fixtures included. Write the
  expected value into the command instead of computing it from a fixture.
- Use `python3 -I`, so that a file the agent wrote, such as `json.py`, is not imported.

### `file_exists`

Passes if `target`, a path relative to the run's folder, exists. **Use it when producing
the file is the point**, or beside another check to make a failure easy to read. It says
nothing about what is in the file.

### `output_matches`

Searches the agent's final answer with the regular expression in `expected` (Python
syntax; `^` and `$` match at each line). **Use it when you asked for an exact form of
answer.** It does not read files. A search that takes over 5 seconds fails the check.

Ask for that form in the prompt. An agent cannot be expected to guess it. Remember that
JSON needs `\\.` for the regular expression's `\.`.

### `llm` checklists

The judge model reads the agent's **final answer** and says whether each item is met,
quoting the answer. All items must be met. **Use it for what the answer says**, such as
whether it explains a result.

- It sees only the answer, not the files. Test files with `exit_code`.
- Make each item one concrete statement that is true or false: "Says that refunded rows
  were left out", not "Is a good answer".
- Do not use it for what a deterministic check can test, such as a number or a name.
- A "met" verdict counts only if the judge's quote is found word for word in the answer.
  Small models often drop Markdown marks when quoting, which fails a right answer, so ask
  for plain text in the prompt.
- The judge is also shown the prompt, and a small model can mix the prompt's requirements
  up with your checklist: asked about the answer, it may fail it because nothing shows a
  file was written. Give an `llm` check a task whose prompt asks only for something said
  in the answer, and read its reasons in the run logs.
- Each item is judged by a model, so it costs calls and can vary between runs.

## Fixtures

- Paths are relative to the folder the task file is in, and must stay inside it.
- Each fixture is copied into the run's folder, `/work`, **under its own name**:
  `fixtures/march/expenses.csv` arrives as `/work/expenses.csv`, and a folder
  `fixtures/project` as `/work/project/`. Two fixtures of one task cannot share a name.
- Every run starts from a fresh copy.
- They must fit in the run's folder, 16 MB unless the config's `sandbox_disk` says
  otherwise.
- Links must be relative and lead to a file in the same fixture. Hard links are refused.
- There is no network, so ship everything the task needs.

The agent's working folder is `/work`. In the prompt, say "in this folder" and use file
names; do not use paths on your machine.

## Do not give the answer away

The scores mean something only if the agent has to do the work.

- **The prompt** states what to produce, not what the result should be.
- **Fixtures** hold the input only. No expected output, answer key or test script.
- **The task file** stays outside the skill.
- **The skill** should not contain the answers to your tasks. A skill that does will
  score well and be useless on other input.

Both runs get the same prompt. So put every requirement a check tests into the prompt,
such as file names, keys and the form of the answer. If only the skill says them, the run
without the skill fails for not knowing what was wanted, and the lift shows nothing about
how well the skill does the work. What the skill should add is *how* to do it well.

Also include a case where the easy answer is wrong (empty input, rows that must be left
out). Before a paid run, ask yourself whether each check could pass for a wrong result, or
fail for a correct one.

## Runs and cost

Each skill takes this many agent sessions:

```text
tasks x runs_per_task x 2 (with and without the skill)
```

`rank` does this for every skill it compares. Two tasks at 3 runs is 12 sessions for one
skill, and 36 to rank three.

- A session is not one model call. The agent may take up to 30 model turns and 600 seconds
  (`agent_max_iterations`, `agent_timeout`).
- Each `llm` check adds a judge call for every session of its task, plus any retries.
- `evaluate` also calls the judge for other dimensions, with or without tasks.

`runs_per_task` is 3 by default. Set it in a config file:

```json
{"runs_per_task": 3}
```

| Runs | Use it for |
|---|---|
| 1 | Trying out a new task file. Robustness cannot be scored, as it compares runs |
| 3 | Normal evaluations |
| 5 or more | Close comparisons, where one unlucky run would change the result |

`/metaskill tasks check <file> --config <config>` prints the session count before you
spend anything.

## Checking a file

```text
/metaskill tasks check <task file> [--config <config file>]
```

It lists every problem it finds, by task: a missing field, a repeated `task_id`, a fixture
that is missing or outside the folder, a check that could not be judged. On a valid file
it shows the tasks, the sessions each skill would take and the judge calls its `llm`
checks would make. It runs nothing, needs no Docker and calls no model. `evaluate` and
`rank` refuse a file with problems before anything runs.

It cannot tell whether a check is *right*: a command with a wrong expected value is a
valid file. Try it with `runs_per_task: 1` and read the run logs in the report folder.
