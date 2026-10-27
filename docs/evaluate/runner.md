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

- `fixtures` are relative to the task file's folder and are returned as absolute paths. They must stay inside that folder: fixtures are copied into the agent's folder, so one outside it could hand the agent any of the user's files (e.g. `~/.hermes/.env`).
- A file that cannot be used raises `TaskError` (a `ValueError`), naming the file and task. Causes: it can't be read or parsed, it has no tasks, a task is not an object or is missing a field, a check is invalid, a `task_id` is duplicated, a `task_id` contains characters other than letters, digits, `_`, `.` and `-` (it becomes part of a log file name), a fixture does not exist, a fixture is outside the task file's folder, a fixture is or contains a link leading out of it or a hard link, or two fixtures of a task have the same name (they are copied by name).

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
| `run_tasks(skill, tasks, agent, *, config, run_dir, ctx=None, judges=None, sandbox=None)` | Runs already-loaded tasks. `sandbox` runs the commands of `exit_code` checks (2.6); `None` means a `DockerSandbox`. `judges` maps a `JudgeType` to a judge and replaces the ones built from `ctx` and `sandbox`, for tests. Raises `ValueError` before running anything if the skill's name is not a safe folder name (2.4) |
| `report_dir(config, now=None)` | `config.output_dir / timestamp(now)`, the folder one evaluate or rank call writes to. Create it once per call and pass it to every skill |
| `timestamp(now=None)` | `YYYY-MM-DD-THH-MM-SS`, used for the folder name and `ReportMeta.timestamp` |

### 2.1 Order and isolation

For each task, then each variant (`with_skill`, then `baseline`), then run `1..config.runs_per_task`:

1. A new sandboxed workspace is opened (2.6) and the task's fixtures are copied into its folder, `/work` (files and folders, by name). For `with_skill`, the skill is copied into `/tmp/skill` (2.2).
2. The agent runs the task's `prompt` in that workspace, with the skill for `with_skill` and `skill=None` for `baseline`. The time is measured around this call only, not the copying.
3. The folder is copied out to a temporary folder on this machine, and every check of the task is judged (2.3). `exit_code` commands run in the workspace itself.
4. The workspace and the copy are removed, and the full log is written (2.4).

No run sees another run's files, and nothing a run does touches this machine.

### 2.2 The agent

The runner does not start Hermes sessions itself. It calls an `Agent`:

```python
class Agent(Protocol):
    def run(self, prompt: str, skill: Skill | None, workspace: Workspace) -> AgentRun: ...

@dataclass
class AgentRun:
    output: str           # the agent's final reply
    tokens: int = 0
    tool_calls: int = 0
    transcript: str = ""  # the full session, for the log
```

The session itself, its model calls, runs on this machine: it needs the user's provider and credentials, which never enter the sandbox. Everything it does runs in `workspace`: every command goes through `workspace.run_command`, every file it reads or writes is in `workspace.root`, and for a `with_skill` run the skill is already in `workspace.skill_dir` (`/tmp/skill`).

**The runner copies the skill, not the agent**, so every `with_skill` run starts from the same files instead of whatever the agent chose to copy. `SKILL.md` and every bundled file in `skill.files` go into `/tmp/skill`, laid out as in the skill's folder and copied as bytes. Links and hard links are left out, as for the judge ([judge.md](judge.md)): either could be a file outside the skill, e.g. `~/.hermes/.env`. If `SKILL.md` itself is one, the run is an error (2.3), not a run without the skill. `baseline` runs get no skill files. `/tmp/skill` is outside `/work`, so the skill does not use up the run's space and the judges do not read it as the run's work.

The Hermes implementation of `Agent` routes Hermes' terminal and file tools through the workspace with a backend of its own that calls `workspace.run_command`, not Hermes' own Docker backend, so every isolation flag stays in `sandbox.py`.

**[OPEN]** That implementation is not built yet: the backend itself, how to load or leave out the skill, and how to read tokens and tool calls from a Hermes session.

If `agent.run` raises, the run is recorded as failed (`passed: false`, `score: 0`, reason `agent failed: ...`). The agent failing is an outcome of the task, not a runner error.

### 2.3 Judging

- Each check is judged by the judge for its `judge` type ([judge.md](judge.md) 1.1). `DeterministicJudge` is built when first needed, with a `run_command` that runs `exit_code` commands in the workspace of the run being judged (2.6). `file_exists` and LLM checks read the copy of the folder made before any check ran, so an `exit_code` command that changes files does not change what they see. The LLM judge is built once per call from `ctx` and `config`, and only if a task has an LLM check.
- A run's checks are combined into one result: `passed` only if every check passed, `score` the mean of the check scores, `reason` the check reasons joined by `; `, and `evidence` the LLM quotes, one per line (`null` if there are none).
- `TestResult.judge` is `llm` if any of the task's checks is an LLM check, otherwise `deterministic`.

A run that cannot be judged is left out of `tests` and listed in `RunnerResult.errors` instead, so a missing judge does not count as a failed task:

- **The LLM judge is unavailable** (`JudgeUnavailable`, e.g. no `ctx`): the task's runs are not started at all, since they could not be judged.
- **The sandbox cannot run** (e.g. Docker is not installed): no run is started, since every run happens in it. The error says why, e.g. `SandboxUnavailable: agent runs and exit_code checks run in a sandbox, which cannot run: Docker is not installed (...)`.
- **The judge failed** (`JudgeError`), or a check is broken (e.g. an invalid regex): the run is listed with the error. So is a workspace that could not be opened, could not take the fixtures (e.g. they are over `config.sandbox_disk`) or the skill (e.g. a hard-linked `SKILL.md`), or could not be copied out.
- **A file operation failed** (`OSError`, e.g. a fixture removed after the task file was loaded, or the log could not be written): the same. The runs before and after it still count.

### 2.4 Logs and output

Each run writes its full log to `run_dir/[skill]/logs/[task_id]-[variant]-r[run].txt` (evaluate.md section 2). `[skill]` is the skill's name, which comes from the skill's own frontmatter, so it must be letters, digits, `_`, `.` and `-`, starting with a letter or digit, like a `task_id`; otherwise a name such as `../x` or an absolute path would put the logs outside `run_dir`. The log holds the metrics, the verdict, the prompt, the full output and the agent's transcript if it gave one.

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

### 2.6 Sandbox for agent runs and `exit_code` checks (`sandbox.py`)

The skill under test steers the agent, so everything the agent runs is untrusted code. So is an `exit_code` check's command (`python test.py`), which runs files the agent could have replaced ([judge.md](judge.md) 1.3). Both run in one `Workspace` per run, and never on this machine:

```python
class Sandbox(Protocol):
    def unavailable(self) -> str: ...                     # why runs cannot start, or ""
    def open(self, fixtures: list[Path]) -> Workspace: ...  # raises JudgeError

class Workspace(Protocol):
    root: str                                             # "/work"
    skill_dir: str                                        # "/tmp/skill"
    def run_command(self, command: str, timeout: float, input: bytes | None = None) -> CommandResult: ...
    def put(self, paths: list[Path], folder: str) -> None: ...  # copy files in
    def export(self, dest: Path) -> None: ...             # copy root out, for the judges
    def close(self) -> None: ...                          # remove it, with every process in it

@dataclass
class CommandResult:
    exit_code: int | None  # None if it timed out
    stdout: str            # last 2,000 characters
    stderr: str            # last 2,000 characters
```

The sandbox is asked `unavailable()` once per runner call, before the first run. `exit_code` commands run after the agent, in the same workspace, before it is closed.

`DockerSandbox(image=DEFAULT_IMAGE, disk=config.sandbox_disk)` is the default. The image is `python:3.12-slim`, pinned by digest so the tag moving cannot change what runs; updating it means bumping the digest in `sandbox.py`. Each run gets a new container (`sleep infinity`), and every command is a `docker exec` into it:

| Requirement (judge.md 1.3) | How |
|---|---|
| Nothing of the user's is readable or writable | Nothing of this machine is mounted. `--read-only` system; the only writable places are `/work` and a small `/tmp` |
| The run cannot fill the disk | `/work` is a size-limited tmpfs volume: 16 MB by default (`config.sandbox_disk`, `disk`) and 4,096 files and folders (`files`), in memory, counted against the container's memory limit (512 MB), so a `sandbox_disk` near that leaves the run little memory. Writes past it fail with `No space left on device` |
| No network | `--network none`, with no allowlist or proxy: a task that needs a package or data ships it as a fixture |
| Clean environment | Docker passes none of the host's variables; only `PATH` and `HOME=/tmp` are set |
| Programs found on the sandbox's `PATH`, never in `/work` | `PATH` is the image's system folders; `/work` is not on it |
| Output cannot fill memory or disk | Only the last 2,000 characters of stdout and stderr are kept, read in memory as they come |
| On timeout, the command is killed | `timeout -s KILL` in the container; returns `None`. A deadline here (timeout + 5 s) covers Docker not answering |
| Everything left behind is killed | closing removes the container, with every process still in it |

Also `--cap-drop ALL`, `no-new-privileges`, limits on memory (512 MB), CPUs (1) and processes (256), and every command runs as `nobody` (65534). `/work` and `/tmp` allow running files (`exec`), since the agent may run scripts it wrote or the skill's scripts in `/tmp/skill`. `/tmp` is 64 MB, separate from `/work`.

- **Fixtures** go in as a tar archive, extracted in the container (`put`). Fixtures over `config.sandbox_disk` do not fit, and the run is an error.
- **Background processes.** A command that leaves a process holding its output open (`server &`) keeps `docker exec` waiting, so the command counts as timed out. This is accepted: the process is killed with the container either way.
- **The judges read a copy.** `export` has `tar` archive `/work` in the container, reads at most 64 MB of it, or twice `sandbox_disk` if that is more (the folder's size and 4,096 entries keep it below that), and extracts it with tarfile's `data` filter. What the filter refuses (absolute paths, links leading out of the folder, devices) and names this system cannot create are skipped, not errors, so a run cannot leave itself unjudged by making them.
- **Exit codes are the command's own.** A code Docker itself might use (125, 126, 127) only fails the check; it is never taken as a Docker failure, so a command cannot imitate one to leave itself unjudged.
- **Docker is required for `evaluate`**, and the image must be pulled first (`docker pull python:3.12-slim@sha256:...`, the digest in `sandbox.DEFAULT_IMAGE`). Evaluate lists both before it starts if they are missing ([dependencies.md](dependencies.md)). The image is never pulled during a run (`--pull never`). `check()` says what is wrong (`not-installed`, `not-running` or `no-image`); `unavailable()` gives the same as a message.

Only the agent's actions are in the sandbox: its model calls stay on this machine (2.2).

## 3. Testing

[`tests/test_runner.py`](../../tests/test_runner.py) uses `FakeAgent`, which writes files and returns canned `AgentRun`s, a fake `ctx.llm` for LLM checks and a `FakeSandbox`, whose workspaces are plain folders, for every run. No real agent, model or container is used. [`tests/test_sandbox.py`](../../tests/test_sandbox.py) checks `DockerSandbox`'s arguments and results with Docker faked, how output is kept with a real process of its own, plus one test against real Docker that is skipped where Docker is not installed or the image is not pulled. [`tests/test_tasks.py`](../../tests/test_tasks.py) covers loading and every `TaskError`.
