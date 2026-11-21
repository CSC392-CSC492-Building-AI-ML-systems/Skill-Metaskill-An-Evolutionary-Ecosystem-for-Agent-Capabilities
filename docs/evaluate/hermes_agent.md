# `hermes_agent.py` and `hermes_child.py` — Hermes as the runner's agent

The runner ([runner.md](runner.md)) runs every task in a sandbox container and asks an `Agent` to do the task there. `HermesAgent` is that agent as a real Hermes session: its model calls run on this machine with the user's Hermes provider, and **everything its tools do runs in the run's container**. Nothing the agent runs can touch this machine.

```python
from metaskill.evaluate.hermes_agent import HermesAgent
from metaskill.evaluate.runner import run_skill

result = run_skill(skill, "tasks.json", HermesAgent(), config=config, run_dir=run_dir)
```

| Setting | Default | Meaning |
|---|---|---|
| `model`, `provider` | `None` | The Hermes model and provider; `None` is the user's configured default |
| `max_iterations` | 30 | Model turns per session |
| `timeout` | 600 s | A whole session; past it the session and everything it started are stopped |
| `hermes` | `"hermes"` | The Hermes launcher, used to find Hermes' interpreter |

Evaluate uses it for every task run when it is given a task file ([evaluate.md](../evaluate.md) 7), with `agent_model` and `agent_provider` from the config, and calls `check()` once first, so a Hermes that cannot be set up is one error before any run. `check()`'s message never holds the child's stderr, which can name this machine's paths; it is on the exception's `stderr`.

## 1. How a run works

```
runner (any Python)                 child (Hermes' Python, one per run)        container
───────────────────                 ──────────────────────────────────         ─────────
open workspace, copy skill ──────────────────────────────────────────────────► /work, /tmp/skill
HermesAgent.run ── request.json ──► register our backend, bind scope,
                                    check guards, build AIAgent ── tools ──► docker exec … bash -c
                ◄── result.json ─── run_conversation, usage, transcript
AgentRun ──► judged by the runner                                               removed by the runner
```

1. **The child process.** `HermesAgent` reads Hermes' launch command (`hermes --print-runtime-command`) and replaces its last step, `runpy.run_module('hermes_cli.main', …)`, with running `hermes_child.py`. So the child gets exactly Hermes' interpreter, isolation flags (`-I`), `sys.path`, `HERMES_HOME` and bootstrap, and no path is hard-coded. A launch command of any other shape is refused as an unsupported Hermes version, rather than guessed at.
2. **Our backend.** The child registers a Hermes terminal backend, `metaskill_sandbox` (a `TerminalEnvironmentProvider`, Hermes' plugin interface for backends). Its environment is a `BaseEnvironment` whose only job is to start `docker exec --workdir /work <container> timeout -s KILL N bash -c <command>`; Hermes' base class does the rest (session snapshot, working folder, timeouts, output). Hermes' file tools run as shell commands through the same backend, so reads, writes, patches and searches happen in the container too. The container's own `timeout` stops a command there, since killing `docker exec` here would leave it running. The backend never creates or removes the container: the runner owns it.
3. **The scope.** Hermes takes the backend from `TERMINAL_ENV` for the whole process, and the user's `config.yaml` `terminal:` keys override that variable. The child binds a *terminal scope* (Hermes' per-context terminal policy): the profile's complete policy with only the backend (`metaskill_sandbox`) and working folder (`/work`) changed. Hermes' tool threads copy the scope. Doing this in a child process means the user's own Hermes session, in which Metaskill's tool may be running, is never redirected.
4. **The agent.** An `AIAgent` built as `hermes -z` builds one, with only the `terminal` and `file` toolsets, no context files (`SOUL.md`, `AGENTS.md`), no memory, and no saved session. With the skill, it gets the skill as Hermes preloads one (section 3).
5. **The result.** `run_conversation` returns the same usage keys as `hermes -z --usage-file`, so `agent_run_from_hermes_usage` maps them; tool calls are counted from the session's messages, so efficiency's `tool_calls` signal is measured (it is `None` from a usage file). The transcript (messages, tool calls with arguments, tool results cut at 2,000 characters, 200,000 in all) goes into the run's log.

## 2. Guards

Two ways the first prototype sent the agent's commands to **this machine** instead of the container: Hermes' documented per-task override (`register_task_env_overrides(..., {"env_type": ...})`) does not select the terminal backend, and `config.yaml` overrides `TERMINAL_ENV`. One such run sent about 21 KB of this machine's command output to the model provider. So the child refuses to run unless both guards hold:

1. **Hermes resolves our backend.** Before an agent is built, Hermes' own resolver (`_get_env_config()`, which the terminal and file tools use) must name `metaskill_sandbox`.
2. **No other backend can be created.** Hermes creates every terminal and file backend through `_create_environment`; in the child, any backend but ours raises, and Hermes' local backend refuses at its class too. Hermes' callers import `_create_environment` inside functions, which sees the replacement; any module holding the original from an earlier import is rebound as well, so the guard does not depend on how Hermes imports it.

Either failing is a setup error, so no agent runs. The integration test probes both (section 5).

Two more hold the agent to the container and to its own session:

3. **The file tools go through the backend.** Hermes' file tools (`read_file`, `write_file`, `patch`, `search_files`) work on the host's files directly for a backend whose `uses_host_filesystem` is true (only its local one), and through the backend's commands otherwise. Our backend declares it false (and `is_local` false) itself rather than relying on `BaseEnvironment`'s default, so a Hermes that changed that default could not route them to this machine.
4. **Nothing of the session outlives it** (`seal_agent`). After a turn, Hermes can fork the agent to review it and write memories or skills straight into the user's stores, and mirror the turn into an external memory provider; a skill under evaluation could steer either. Both are off today only indirectly (the agent has no `skill_manage` tool, and `skip_memory` leaves no memory provider), so the child also sets `skip_background_review` and refuses an agent that has a memory provider attached (a setup error).

## 3. The skill

The skill is given the way `hermes -s <skill>` preloads one: the same activation note, the `SKILL.md` body, the skill directory, and the bundled files. Two things differ, because Hermes' own message points at the host:

- The skill directory is `/tmp/skill`, the runner's copy in the container. Hermes' message names the host path, which does not exist there.
- Bundled files are read with the file tools or run by absolute path. Hermes suggests its `skill_view` tool, which reads the host's skills folder; the `skills` toolset is not enabled.

Links are left out of the file list, as the runner leaves them out of the copy. Hermes' template expansion in skill text (`preprocess_skill_content`) is not applied. The baseline gets no skill message and no `/tmp/skill`.

## 4. Outcomes

| What happened | `run` returns or raises | The runner records |
|---|---|---|
| The session ended (finished, or ran out of turns) | `AgentRun` | A judged run |
| The agent raised during the session | `RuntimeError` | A failed run: the agent failing is an outcome of the task |
| The session ran past `timeout` | `TimeoutError` | A failed run |
| Hermes not found, unsupported Hermes, a guard refused, the agent could not be built (no provider), or the child died or wrote an unreadable result | `HermesAgentUnavailable` (a `JudgeError`) | An error in `RunnerResult.errors`, not a failed run: nothing ran, so the skill must not be scored for it |
| The environment could not serve the session: Hermes marks it `failed` with a reason such as `model_not_found`, `billing`, `auth`, `rate_limit`, `server_error`, `empty_response` or `loop_error`, or a reason Hermes adds later | `HermesAgentUnavailable`, with Hermes' error and `failure_reason` | An error, not a failed run. Hermes puts its own error text in the final response; judging that as the agent's answer would score the skill on this machine's provider |
| The session failed for a reason the task caused: `failure_reason` or `turn_exit_reason` is one of `TASK_FAILURES` (`context_overflow`, `truncated`, `content_policy_blocked`, `context_compression_exhausted`, `context_compression_timeout`) | `RuntimeError`, with the reason | A failed run. A skill can drive these, so they count against it |

Which side a `failed` session falls on is decided by `hermes_child.failure_status` from Hermes' own reason, and only the reasons in `TASK_FAILURES` count against the skill; any other reason, including one added by a later Hermes, stays an environment error, so the run is not scored. `content_policy_blocked` is in the set because the skill's text can cause it and the baseline run gets the same prompt; it is a team decision and moving it is a one-line change. `loop_error` stays out: it is an exception inside Hermes' own loop, not evidence about the skill.

**The pre-flight check.** `HermesAgent.check(workspace)` sets Hermes up and drives its terminal and file tools once each, with no model. Setup being accepted is not enough, because with Docker stopped or on a Hermes whose tools run in another folder every tool call fails and setup still succeeds. So `check_problems` reads each tool's answer: the file tool wrote `check.txt` into the workspace root, the terminal ran there and exited 0, the file tool read back what the terminal wrote, and every guard refused. Anything else raises `HermesAgentUnavailable` with a short reason for each failure (no tool output or stderr in the message). Checked on 0.21.5, 0.22.2 and 0.23.2 against a real container, and against a container that does not exist.

A timeout stops the child and every process it started, so the `docker exec` processes stop too; commands inside the container stop with it when the runner removes it. Stopping the child alone would not do: `communicate()` waits for the `docker exec` processes, which hold its stderr open. On Linux and macOS the child starts in a session of its own and its process group is sent `SIGKILL`.

Windows has no group signal, so two mechanisms are used together (`winjob.py`, standard library `ctypes` only):

1. **A Job Object.** The child starts suspended (`CREATE_SUSPENDED`, in a new process group), is put in a job created with `KILL_ON_JOB_CLOSE`, and only then is resumed. Everything it starts is therefore in the job, including processes whose parent has already exited. Closing the job (at the end of every session, or if the evaluator itself dies) kills whatever is still in it.
2. **`taskkill /F /T /PID <child>`.** On a timeout this runs first, while the tree's parent links are whole, and then the job is killed. It covers what a job cannot hold: a process Windows starts on the caller's behalf outside the job, such as a Microsoft Store Python's interpreter, which app activation starts. Hermes' bundled Python is a plain executable, so its tree is in the job.

If Windows refuses a job, `taskkill` alone is used. If both fail, the child is killed alone. If the suspended child cannot be resumed, it is killed and the run is `HermesAgentUnavailable`. Checked against a real Hermes child stopped inside a `docker exec`: Hermes' Python, `docker.exe` and `conhost.exe` were all gone after the timeout.

**The working folder on Windows.** Hermes drops a `TERMINAL_CWD` that `os.path.isabs` rejects and uses the backend's default instead, which is `/root` unless the backend names one. On Windows, Python 3.13 and later no longer count a path without a drive as absolute, so `/work` was rejected on Hermes' bundled Python (3.14), and every tool ran in `/root`, where the sandbox's user `nobody` may not go. Linux was not affected. The backend now names the workdir as its `default_cwd` (Hermes' provider interface), which Hermes keeps as is. A `cd` still carries to the next command and to relative file paths. Checked on Windows 11 with Docker Desktop 28.5 and Hermes 0.23.0.

**Hermes 0.23 or later on Windows.** `default_cwd` is in Hermes' provider interface from 0.23 on. On Windows the child refuses an older Hermes as a setup error that names its version (`windows_version_problem`), before any tool runs. A version it cannot read (no version info, or the `0.0.0` install stamp) is let through, and the pre-flight check still refuses tools that do not reach `/work`. Linux and macOS have no minimum beyond what section 6 lists.

## 5. Tests

`tests/test_hermes_agent.py`. The unit tests need neither Hermes nor Docker: a fake child process stands in for the session.

- The skill message points at `/tmp/skill`, never at a host path, never at `skill_view`, and lists exactly the files the runner copies (`runner.copied`: no links, hard links or paths outside the skill).
- Hermes' launch command keeps its bootstrap and runs the child; any other shape, or no Hermes, is unavailable.
- A finished session maps every usage field and the tool calls; the request names the container, the Docker executable and `/work`, and carries a skill message only with the skill.
- A setup error is a `JudgeError`; an agent error is not; a child that dies without a result is unavailable with its error; a timeout stops the child and what it started (a real grandchild that holds its stderr, on any OS; on Windows, also an orphan whose parent has exited, and anything left running after a session that finished; and the job, `taskkill` and fallback paths with the calls faked, on any OS). The two Windows job tests skip on a Microsoft Store Python, whose interpreter starts outside any job; a workspace that is not Docker is unavailable; through the runner, unavailability is an error, not a failed run.
- The child imports only the standard library at the top (it runs on Hermes' interpreter); its backend names the workdir as its default folder; on Windows it refuses a Hermes older than 0.23; its `docker exec` command, tool-call count and transcript are tested directly; a request from another protocol version is refused before Hermes is touched.

Opt-in integration tests need, besides Hermes and Docker:

- **Python 3.12.11, 3.13.4 or later** for the test process. The sandbox refuses older ones because of CVE-2025-4517 (tarfile), and the tests then skip with that reason. Hermes' own bundled Python (3.14) works for a test virtual environment.
- **The sandbox image pulled first** (`docker pull` of the pinned image in `sandbox.DEFAULT_IMAGE`; [dependencies.md](dependencies.md) lists the command). Without it the tests skip.

Hermes 0.21.5, 0.22.2 and 0.23.2 all pass them on Linux, and 0.23.0 on Windows 11 with Docker Desktop; the tests use whichever `hermes` is first on `PATH`. CI runs the unit tests on Linux and Windows; GitHub's Windows runners cannot run Linux containers, so the integration tests are run by hand: the pull-request checklist asks for them whenever the Hermes adapter or the sandbox changes. Run them from a virtual environment made from a plain Python, such as Hermes' bundled one, not from the Microsoft Store.

Opt-in integration tests:

- `METASKILL_HERMES_IT=1` (Hermes and Docker, no model): Hermes' real `write_file`, `terminal` and `read_file` tools reach the container (`/work`, user `nobody`, files written by one read by another), and creating Hermes' local backend, its Docker backend, or `LocalEnvironment` directly is refused. A session on a model the provider does not have is an error, not a judged run (the provider rejects it, so it costs nothing).
- `METASKILL_HERMES_LIVE=1` (also the configured model): one real session per variant through `run_skill`, with no errors and every metric measured.

`seconds` in a run's metrics is the session's own time as the child measured it (`AgentRun.seconds`), which leaves out the one to two seconds Hermes takes to start; an agent that does not measure it gets the runner's clock.

## 6. Hermes internals this relies on

None of these is a stable public API. A change breaks loudly, as a setup error or a failing integration test, never by running on the host.

| Used | Where in Hermes |
|---|---|
| `hermes --print-runtime-command` and its `runpy.run_module('hermes_cli.main', …)` ending | `hermes_cli/_launchers.py` |
| `TerminalEnvironmentProvider`, `terminal_env_registry.register_provider` | `agent/terminal_env_provider.py`, `agent/terminal_env_registry.py` |
| `BaseEnvironment._run_bash`, `_popen_bash` | `tools/environments/base.py`, `base_output.py` |
| `_create_environment`, `LocalEnvironment` (guard 2) | `tools/terminal_tool_backends.py`, `tools/environments/local.py` |
| `build_profile_terminal_scope`, `set_terminal_scope` | `tools/terminal_scope.py` |
| `_get_env_config` (guard 1) | `tools/terminal_tool.py` |
| `load_config`, `resolve_runtime_with_fallback`, `AIAgent`, `run_conversation` | `hermes_cli/`, `run_agent.py` |
| `model_switch.resolve_startup_model_route`, `direct_alias_runtime_request`, `DIRECT_ALIASES`; `models.detect_provider_for_model` (model and provider choice) | `hermes_cli/` |
| `toolsets.resolve_toolset`, `tools.tool_search` (`ToolSearchConfig`, `load_config`), and the `version_info` module (the version in the transcript) | `toolsets.py`, `tools/`, `hermes_cli/` |

Built and tested against Hermes 0.21.5 and 0.23.2; the version is recorded in each run's transcript. Hermes 0.22 moved `hermes -z` behind its gateway and removed `hermes_cli.oneshot._resolve_model_and_provider`, so `hermes_child.py` has its own copy of that choice (the request's model and provider, else the configured ones), built on the helpers above. Hermes 0.22.2 itself was not tested here.

## 7. Limits and open questions

- **The tool-search bridge (Hermes 0.21.5 and 0.23.2 both have it).** Hermes puts `tool_search`/`tool_describe`/`tool_call` in front of the tools, and the bridge also reaches the user's connected apps (mail, calendars, issue trackers) and can install MCP servers, none of which runs in the sandbox. The child turns the bridge off for its process (`_turn_off_tool_search`), and `_check_tools` then compares the agent's tools with the terminal and file toolsets (`terminal`, `process_manage`, `read_file`, `write_file`, `patch`, `search_files` on 0.23.2). Any other tool is a setup error and nothing runs. An earlier version of this section said the bridge only searched the enabled tools; the connector and MCP paths show that was wrong.
- **Windows** is supported with Hermes 0.23 or later (section 4: the timeout, the working folder and the minimum version). It was checked on 0.23.0.
- **The user's own hooks and plugins** load in the child as in any Hermes session, and run on this machine. They are the user's configuration, as trusted as their Hermes; the skill's commands never reach them.
- **Start-up cost.** Each run starts a Hermes process, about one to two seconds.
- **Cost.** Every run is real model calls on the user's provider ([evaluate.md](../evaluate.md) 9).
