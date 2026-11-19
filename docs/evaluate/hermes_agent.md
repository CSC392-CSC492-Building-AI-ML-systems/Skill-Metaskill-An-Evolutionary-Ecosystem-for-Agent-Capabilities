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

It is not yet called by `evaluate` itself: wiring the runner into evaluate is a separate step ([evaluate.md](../evaluate.md) 3.5).

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
| The model provider could not serve the session: Hermes marks it `failed` (model unavailable, billing or entitlement, retries exhausted, a content-policy block) | `HermesAgentUnavailable`, with Hermes' error and `failure_reason` | An error, not a failed run. Hermes puts its own error text in the final response; judging that as the agent's answer would score the skill on this machine's provider |

A timeout stops the child's whole process group, so the `docker exec` processes it started stop too; commands inside the container stop with it when the runner removes it.

## 5. Tests

`tests/test_hermes_agent.py`. The unit tests need neither Hermes nor Docker: a fake child process stands in for the session.

- The skill message points at `/tmp/skill`, never at a host path, never at `skill_view`, and leaves out links.
- Hermes' launch command keeps its bootstrap and runs the child; any other shape, or no Hermes, is unavailable.
- A finished session maps every usage field and the tool calls; the request names the container, the Docker executable and `/work`, and carries a skill message only with the skill.
- A setup error is a `JudgeError`; an agent error is not; a child that dies without a result is unavailable with its error; a timeout stops the child and what it started; a workspace that is not Docker is unavailable; through the runner, unavailability is an error, not a failed run.
- The child imports only the standard library at the top (it runs on Hermes' interpreter); its `docker exec` command, tool-call count and transcript are tested directly; a request from another protocol version is refused before Hermes is touched.

Opt-in integration tests:

- `METASKILL_HERMES_IT=1` (Hermes and Docker, no model): Hermes' real `write_file`, `terminal` and `read_file` tools reach the container (`/work`, user `nobody`, files written by one read by another), and creating Hermes' local backend, its Docker backend, or `LocalEnvironment` directly is refused. A session on a model the provider does not have is an error, not a judged run (the provider rejects it, so it costs nothing).
- `METASKILL_HERMES_LIVE=1` (also the configured model): one real session per variant through `run_skill`, with no errors and every metric measured.

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

- **Not wired into `evaluate` yet.** `evaluate_skill` does not run tasks; that is the next step, together with where a skill's tasks come from.
- **The tool-search bridge (Hermes 0.21.5 and 0.23.2 both have it).** Hermes puts `tool_search`/`tool_describe`/`tool_call` in front of the tools, and the bridge also reaches the user's connected apps (mail, calendars, issue trackers) and can install MCP servers, none of which runs in the sandbox. The child turns the bridge off for its process (`_turn_off_tool_search`), and `_check_tools` then compares the agent's tools with the terminal and file toolsets (`terminal`, `process_manage`, `read_file`, `write_file`, `patch`, `search_files` on 0.23.2). Any other tool is a setup error and nothing runs. An earlier version of this section said the bridge only searched the enabled tools; the connector and MCP paths show that was wrong.
- **The user's own hooks and plugins** load in the child as in any Hermes session, and run on this machine. They are the user's configuration, as trusted as their Hermes; the skill's commands never reach them.
- **Start-up cost.** Each run starts a Hermes process, about one to two seconds.
- **Cost.** Every run is real model calls on the user's provider ([evaluate.md](../evaluate.md) 9).
