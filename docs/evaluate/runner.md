# `runner.py` and `tasks.py` — Running test tasks

Code: [`metaskill/evaluate/runner.py`](../../metaskill/evaluate/runner.py), [`metaskill/evaluate/tasks.py`](../../metaskill/evaluate/tasks.py). Design: [evaluate.md](../evaluate.md), sections 2.1, 4 and 6.

`runner.py` is the only module that runs the agent. For each task it runs the agent `n` times with the skill and `n` times without it, judges every run with [`judge.py`](judge.md), and returns one `TestResult` per run. `quality.py`, `robustness.py` and `efficiency.py` compute their scores from those results, so all three share one set of runs.

## 1. Task files (`tasks.py`)

`load_tasks(path)` reads a JSON or YAML file (YAML needs PyYAML). The file holds either a list of tasks or an object with a `tasks` list. Each task has the shape of `Task.to_dict()` ([models.md](models.md)):

```json
{
  "tasks": [{
    "task_id": "upload-single-file",
    "description": "Upload ./a.txt to bucket X",
    "prompt": "Upload a.txt to the bucket",
    "checks": [
      {"judge": "deterministic", "kind": "file_exists", "target": "uploaded.txt"},
      {"judge": "llm", "checklist": ["names the bucket it uploaded to"]}
    ],
    "fixtures": ["files/a.txt"]
  }]
}
```

- `fixtures` are relative to the task file's folder and are returned as absolute paths.
- A file that cannot be used raises `TaskError` (a `ValueError`), naming the file and task. Causes: it can't be read or parsed, it has no tasks, a task is not an object or is missing a field, a check is invalid, a `task_id` is duplicated, a `task_id` contains characters other than letters, digits, `_`, `.` and `-` (it becomes part of a log file name), or a fixture does not exist.

## 2. Running (`runner.py`)

```python
from metaskill.evaluate.runner import report_dir, run_skill

run_dir = report_dir(config)        # evaluation-reports/2026-10-14-T15-30-02
result = run_skill(skill, "tasks.json", agent, config=config, run_dir=run_dir, ctx=ctx)
result.tests                        # list[TestResult], the report's `tests`
```

| Function | Description |
|---|---|
| `run_skill(skill, tasks_file, agent, *, config, run_dir, ctx=None, sandbox=None)` | Loads the tasks with `load_tasks`, then calls `run_tasks` |
| `run_tasks(skill, tasks, agent, *, config, run_dir, ctx=None, judges=None, sandbox=None)` | Runs already-loaded tasks. `sandbox` runs the commands of `exit_code` checks (2.6); `None` means a `DockerSandbox`. `judges` maps a `JudgeType` to a judge and replaces the ones built from `ctx` and `sandbox`, for tests |
| `report_dir(config, now=None)` | `config.output_dir / timestamp(now)`, the folder one evaluate or rank call writes to. Create it once per call and pass it to every skill |
| `timestamp(now=None)` | `YYYY-MM-DD-THH-MM-SS`, used for the folder name and `ReportMeta.timestamp` |

### 2.1 Order and isolation

For each task, then each variant (`with_skill`, then `baseline`), then run `1..config.runs_per_task`:

1. A new temporary folder is made and the task's fixtures are copied into it (files and folders, by name).
2. The agent runs the task's `prompt` in that folder, with the skill for `with_skill` and `skill=None` for `baseline`. The time is measured around this call.
3. Every check of the task is judged (2.3).
4. The full log is written (2.4), and the folder is deleted.

No run sees another run's files.

### 2.2 The agent

The runner does not start Hermes sessions itself. It calls an `Agent`:

```python
class Agent(Protocol):
    def run(self, prompt: str, skill: Skill | None, workdir: Path) -> AgentRun: ...

@dataclass
class AgentRun:
    output: str           # the agent's final reply
    tokens: int = 0
    tool_calls: int = 0
    transcript: str = ""  # the full session, for the log
```

**[OPEN]** The Hermes implementation of `Agent` is not built yet: how to load or leave out the skill, set the working folder, and read tokens and tool calls from a Hermes session.

If `agent.run` raises, the run is recorded as failed (`passed: false`, `score: 0`, reason `agent failed: ...`). The agent failing is an outcome of the task, not a runner error.

### 2.3 Judging

- Each check is judged by the judge for its `judge` type ([judge.md](judge.md) 1.1). `DeterministicJudge` is built when first needed, with the sandbox's `run_command` for `exit_code` checks (2.6). The LLM judge is built once per call from `ctx` and `config`, and only if a task has an LLM check.
- A run's checks are combined into one result: `passed` only if every check passed, `score` the mean of the check scores, `reason` the check reasons joined by `; `, and `evidence` the LLM quotes, one per line (`null` if there are none).
- `TestResult.judge` is `llm` if any of the task's checks is an LLM check, otherwise `deterministic`.

A run that cannot be judged is left out of `tests` and listed in `RunnerResult.errors` instead, so a missing judge does not count as a failed task:

- **The LLM judge is unavailable** (`JudgeUnavailable`, e.g. no `ctx`): the task's runs are not started at all, since they could not be judged.
- **The sandbox cannot run** (e.g. Docker is not installed) and the task has an `exit_code` check: the same, its runs are not started. The error says why, e.g. `JudgeUnavailable: exit_code checks run in a sandbox, which cannot run: Docker is not installed (...)`.
- **The judge failed** (`JudgeError`), or a check is broken (e.g. an invalid regex): the run is listed with the error.

### 2.4 Logs and output

Each run writes its full log to `run_dir/[skill]/logs/[task_id]-[variant]-r[run].txt` (evaluate.md section 2). The log holds the metrics, the verdict, the prompt, the full output and the agent's transcript if it gave one.

`TestResult.output` is the output cut to `config.output_max_chars`, followed by the path to the full log, relative to the run folder: `... (full log: cleanup/logs/write-report-with_skill-r1.txt)`.

### 2.5 What it returns

`RunnerResult`:

| Field | Description |
|---|---|
| `tests` | One `TestResult` per task, variant and run that could be judged, in run order. This is the per-skill `tests` list of the report (evaluate.md 2.1) |
| `errors` | `"[task_id] [variant] r[run]: [error]"` for every run left out of `tests` |
| `judge_model` | `"provider/model"` of every LLM judge reply, for `ReportMeta.judge_model`; empty if no LLM check ran |
| `judge_usage` | Tokens the LLM judge used (`TokenUsage`) |

The runner writes only the logs. Writing `ranked-evaluation.json` and `.md` and computing scores belong to the scoring modules, `rank.py` and `__init__.py`.

### 2.6 Sandbox for `exit_code` checks (`sandbox.py`)

An `exit_code` check runs a command, usually on files in the run's folder (`python test.py`). The agent run, steered by the skill under test, could have replaced those files, so the command is untrusted code and never runs on this machine ([judge.md](judge.md) 1.3). The runner gives `DeterministicJudge` the sandbox's `run_command`:

```python
class Sandbox(Protocol):
    def unavailable(self) -> str: ...  # why commands cannot run, or ""
    def run_command(self, command: str, workdir: Path, timeout: float) -> tuple[int | None, str]: ...
```

The sandbox is asked `unavailable()` once per runner call, before the first task with an `exit_code` check, and not at all if no task has one. The command runs after the agent, in the run's folder, before the folder is deleted.

`DockerSandbox(image="python:3.12-slim")` is the default. Each command runs in a new container, removed afterwards:

| Requirement (judge.md 1.3) | How |
|---|---|
| Only `workdir` is writable, nothing of the user's is readable | `workdir` is the only mount, at `/work`; `--read-only` system, a small `/tmp` tmpfs |
| No network | `--network none` |
| Clean environment | Docker passes none of the host's variables; only `PATH` and `HOME=/tmp` are set |
| Programs found on the sandbox's `PATH`, never in `workdir` | `PATH` is the image's system folders; `/work` is not on it |
| No stdin, stdout discarded, stderr to a file | stderr goes to a temporary file outside `workdir`; its last 2,000 characters are kept |
| On timeout, everything is killed | the container is removed, which kills every process in it; returns `None` |
| Background processes killed when the command ends | `--init`: when the command ends the container stops, with everything in it |

Also `--cap-drop ALL`, `no-new-privileges`, and limits on memory (512 MB), CPUs (1) and processes (256). On Linux and macOS the command runs as the user's own uid, so the runner can delete what it wrote.

- **The image must be pulled first** (`docker pull python:3.12-slim`). It is never pulled during a check (`--pull never`), which would need the network and count against the check's timeout. If it is missing, `unavailable()` says to pull it.
- **The exit code is read from the container** (`docker inspect`), not from `docker run`, whose own failures exit with 125. Otherwise a command could exit 125 to look like a Docker failure and leave itself unjudged. A real Docker failure (the container did not start, or Docker reports an error) raises `JudgeError`.
- The check's timeout includes the container's start, about a second.

**[OPEN]** The agent's own run is not sandboxed yet; that belongs to the Hermes `Agent` (2.2). When it is, the check should run in the same sandbox.

## 3. Testing

[`tests/test_runner.py`](../../tests/test_runner.py) uses `FakeAgent`, which writes files and returns canned `AgentRun`s, a fake `ctx.llm` for LLM checks and a `FakeSandbox` for `exit_code` checks. No real agent, model or container is used. [`tests/test_sandbox.py`](../../tests/test_sandbox.py) checks `DockerSandbox`'s arguments and results with Docker faked, plus one test against real Docker that is skipped where Docker is not installed or the image is not pulled. [`tests/test_tasks.py`](../../tests/test_tasks.py) covers loading and every `TaskError`.
