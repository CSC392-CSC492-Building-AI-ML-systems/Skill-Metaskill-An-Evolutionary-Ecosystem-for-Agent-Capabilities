"""The Hermes side of HermesAgent: one agent session whose tools run in the sandbox.

hermes_agent.py starts this file with Hermes' own interpreter and bootstrap (the argv from
`hermes --print-runtime-command`), one process per session, with a request file. It runs a
Hermes AIAgent whose terminal and file tools reach the run's Docker container through a
backend of ours, and writes a result file. See docs/evaluate/hermes_agent.md.

Why a separate process: Hermes picks the terminal backend from TERMINAL_ENV for the whole
process (a per-task override does not select it), and the user's config.yaml overrides that
variable. Here a terminal scope sets the backend for this process alone, so the user's own
Hermes session is never redirected, and nothing in this process can fall back to the host:

1. Hermes' own resolver must name our backend before an agent is built (_check_backend).
2. Creating any other backend, the host's local one included, raises (_refuse_other_backends).

Either failing is a setup error: nothing ran. This module imports only the standard
library at the top, and nothing from metaskill, so it runs wherever Hermes does.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = "metaskill_sandbox"
PROTOCOL = 1  # bumped when the request or result shape changes
TOOLSETS = ("terminal", "file")
# The in-container `timeout` gets this much more than Hermes' own command timeout, so
# Hermes' timeout fires first and the container's only stops what Hermes could not.
KILL_GRACE = 5
MAX_TRANSCRIPT_CHARS = 200_000
MAX_TOOL_TEXT_CHARS = 2_000


class SetupError(Exception):
    """Hermes or the sandbox backend could not be set up safely; no agent ran."""


# --- Pure helpers (tested without Hermes) ----------------------------------------------


def docker_argv(docker: str, container: str, workdir: str, command: str, *,
                login: bool, timeout: float, stdin: bool) -> list[str]:
    """`docker exec` running bash in the run's container. The container's own `timeout`
    kills the command there: killing `docker exec` on this machine would leave it running."""
    return [docker, "exec", *(["-i"] if stdin else []), "--workdir", workdir, container,
            "timeout", "-s", "KILL", str(int(timeout) + KILL_GRACE),
            "bash", *(["-l"] if login else []), "-c", command]


def count_tool_calls(messages: list[dict[str, Any]]) -> int:
    """Tool calls the model made in the session (what efficiency scores as tool_calls)."""
    return sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")


def _clip(text: Any, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    return text if len(text) <= limit else f"{text[:limit]} [... {len(text) - limit} more chars]"


def transcript(messages: list[dict[str, Any]], header: str = "") -> str:
    """The session as text for the run's log: each message, each tool call with its
    arguments, each tool result (long ones cut). Capped at MAX_TRANSCRIPT_CHARS."""
    lines = [header] if header else []
    for m in messages:
        role = m.get("role", "?")
        if role == "tool":
            lines.append(f"[tool result: {m.get('name') or m.get('tool_call_id', '')}] "
                         f"{_clip(m.get('content') or '', MAX_TOOL_TEXT_CHARS)}")
            continue
        if m.get("content"):
            lines.append(f"[{role}] {_clip(m['content'], MAX_TOOL_TEXT_CHARS * 4)}")
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            lines.append(f"[tool call: {fn.get('name', '?')}] "
                         f"{_clip(fn.get('arguments') or '', MAX_TOOL_TEXT_CHARS)}")
    return _clip("\n".join(lines), MAX_TRANSCRIPT_CHARS)


# --- Hermes setup ----------------------------------------------------------------------


def _register_backend(docker: str, container: str, workdir: str) -> None:
    from agent import terminal_env_registry
    from agent.terminal_env_provider import TerminalEnvironmentProvider
    from tools.environments.base import BaseEnvironment
    from tools.environments.base_output import _popen_bash

    class SandboxEnvironment(BaseEnvironment):
        """Runs every command in the runner's container. It never creates or removes the
        container: the runner owns it, so every isolation flag stays in sandbox.py."""

        def __init__(self, cwd: str, timeout: int):
            super().__init__(cwd=cwd, timeout=timeout)
            self.init_session()

        def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
            return _popen_bash(docker_argv(docker, container, workdir, cmd_string, login=login,
                                           timeout=timeout, stdin=stdin_data is not None),
                               stdin_data)

        def cleanup(self):
            pass

    class SandboxProvider(TerminalEnvironmentProvider):
        is_remote = True     # do not describe this machine's OS, home or cwd to the model
        is_container = True  # its own filesystem: no host-approval prompts in a throwaway box

        @property
        def name(self) -> str:
            return BACKEND

        def is_available(self) -> bool:
            return True

        def create_environment(self, *, cwd, timeout, task_id="default", **kwargs):
            inside = isinstance(cwd, str) and (cwd == workdir or cwd.startswith(workdir + "/"))
            return SandboxEnvironment(cwd if inside else workdir, timeout)

    terminal_env_registry.register_provider(SandboxProvider())


def _refuse_other_backends() -> None:
    """Guard 2: no backend but ours can be created in this process. Hermes creates every
    terminal and file backend through _create_environment, and the local backend is
    refused at its class too, in case a path reaches it another way."""
    import tools.environments.local as local
    import tools.terminal_tool_backends as backends

    def refuse_local(self, *args, **kwargs):
        raise SetupError("refusing to create Hermes' local backend: it would run on this machine")

    local.LocalEnvironment.__init__ = refuse_local
    create = backends._create_environment

    def only_ours(env_type, *args, **kwargs):
        if env_type != BACKEND:
            raise SetupError(f"refusing to create a {env_type!r} backend; only {BACKEND} may run")
        return create(env_type, *args, **kwargs)

    # Hermes' callers import it inside functions today, which sees the patched attribute;
    # a module that imported it at load time would hold the original, so rebind those too.
    for module in list(sys.modules.values()):
        for name, value in list(getattr(module, "__dict__", {}).items()):
            if value is create:
                setattr(module, name, only_ours)


def _bind_scope(workdir: str) -> None:
    """The backend for this process: the profile's whole terminal policy, with only the
    backend and the working folder changed. config.yaml's `terminal:` keys override the
    TERMINAL_* variables, so setting those alone is not enough; a scope replaces both, and
    Hermes' tool threads copy it."""
    from tools.terminal_scope import build_profile_terminal_scope, set_terminal_scope

    scope = dict(build_profile_terminal_scope(os.environ["HERMES_HOME"]))
    scope.update({"TERMINAL_ENV": BACKEND, "TERMINAL_CWD": workdir})
    set_terminal_scope(scope)


def _check_backend() -> None:
    """Guard 1: Hermes' own resolver must name our backend before any agent is built."""
    from tools.terminal_tool import _get_env_config

    env_type = _get_env_config().get("env_type")
    if env_type != BACKEND:
        raise SetupError(f"Hermes resolves the terminal backend to {env_type!r}, not {BACKEND}")


def _setup(request: dict[str, Any]) -> None:
    try:
        _register_backend(request["docker"], request["container"], request["workdir"])
        _refuse_other_backends()
        _bind_scope(request["workdir"])
        _check_backend()
    except SetupError:
        raise
    except Exception as e:  # an internal Hermes API this relies on changed
        raise SetupError(f"Hermes setup failed: {type(e).__name__}: {e}") from e


def _build_agent(request: dict[str, Any]):
    """An AIAgent built as `hermes -z` builds one (hermes_cli/oneshot._run_agent), with
    only the terminal and file tools, no context files, no memory and no saved session."""
    from hermes_cli.config import load_config
    from hermes_cli.oneshot import _resolve_model_and_provider
    from hermes_cli.runtime_provider import resolve_runtime_with_fallback
    from run_agent import AIAgent

    cfg = load_config()
    choice = _resolve_model_and_provider(cfg, request.get("model"), request.get("provider"))
    runtime, _ = resolve_runtime_with_fallback(
        cfg, requested=choice.provider, target_model=choice.model or None,
        explicit_base_url=choice.base_url, explicit_api_key=choice.api_key)
    agent = AIAgent(
        api_key=runtime.get("api_key"), base_url=runtime.get("base_url"),
        provider=runtime.get("provider"), requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode") or choice.api_mode, model=choice.model,
        credential_pool=runtime.get("credential_pool"),
        request_overrides=runtime.get("request_overrides"),
        enabled_toolsets=list(TOOLSETS), quiet_mode=True, platform="batch",
        skip_context_files=True, skip_memory=True,
        max_iterations=int(request["max_iterations"]),
        ephemeral_system_prompt=request.get("skill_message") or None)
    agent.suppress_status_output = True
    return agent, choice.model, runtime.get("provider")


def _hermes_version() -> str:
    try:
        from hermes_cli import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def _check_mode(request: dict[str, Any]) -> dict[str, Any]:
    """No model: drive Hermes' real terminal and file tools once each, to show they reach
    the container (the integration test). Returns what each returned."""
    from tools.file_tools import read_file_tool, write_file_tool
    from tools.terminal_tool import terminal_tool

    task = "metaskill-check"
    written = write_file_tool("check.txt", "written by the file tool", task_id=task)
    ran = terminal_tool("pwd; id -un; cat check.txt; echo from-terminal > check2.txt",
                        task_id=task)
    read = read_file_tool("check2.txt", task_id=task)
    return {"write_file": written, "terminal": ran, "read_file": read, "guards": _probe_guards()}


def _probe_guards() -> dict[str, str]:
    """Try to create the backends the guards must refuse; each entry says what happened."""
    import tools.terminal_tool_backends as backends
    from tools.environments.local import LocalEnvironment

    probes = {
        "local backend": lambda: backends._create_environment("local", "", "/", 10),
        "docker backend": lambda: backends._create_environment("docker", "", "/", 10),
        "LocalEnvironment class": lambda: LocalEnvironment(cwd="/", timeout=10),
    }
    seen = {}
    for name, probe in probes.items():
        try:
            probe()
            seen[name] = "CREATED"
        except SetupError as e:
            seen[name] = f"refused: {e}"
        except Exception as e:
            seen[name] = f"failed otherwise: {type(e).__name__}: {e}"
    return seen


def run(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("protocol") != PROTOCOL:
        raise SetupError(f"request protocol {request.get('protocol')!r}, expected {PROTOCOL}")
    _setup(request)
    out: dict[str, Any] = {"hermes_version": _hermes_version()}
    if request.get("check"):
        out.update(status="ok", check=_check_mode(request))
        return out
    try:
        agent, model, provider = _build_agent(request)
    except Exception as e:
        raise SetupError(f"could not build the Hermes agent: {type(e).__name__}: {e}") from e
    out.update(model=model, provider=provider,
               tools=sorted(t["function"]["name"] for t in (agent.tools or [])))
    start = time.monotonic()
    try:
        result = agent.run_conversation(request["prompt"], task_id="metaskill-run")
    except Exception as e:  # the agent failing is an outcome of the task
        out.update(status="agent_error", error=f"{type(e).__name__}: {e}")
        return out
    finally:
        try:
            agent.close()
        except Exception:
            pass
    messages = result.get("messages") or []
    if result.get("failed"):
        # Hermes marks a session the model provider could not serve (model unavailable,
        # billing, retries exhausted, a content-policy block) as failed, and puts its own
        # error text in final_response. That text is not the agent's answer and must not
        # be judged as one: the run tells nothing about the skill.
        out.update(status="provider_error",
                   error=str(result.get("error") or "the model provider failed"),
                   failure_reason=result.get("failure_reason"))
        return out
    out.update(
        status="ok", seconds=round(time.monotonic() - start, 3),
        final_response=result.get("final_response") or "",
        usage={k: result.get(k) for k in ("input_tokens", "output_tokens", "reasoning_tokens",
                                          "cache_read_tokens", "cache_write_tokens", "api_calls",
                                          "completed", "partial", "failed", "turn_exit_reason")},
        tool_calls=count_tool_calls(messages),
        transcript=transcript(messages, header=f"Hermes {out['hermes_version']}, model {model} "
                                               f"({provider}), tools {', '.join(out['tools'])}"),
    )
    return out


def main(argv: list[str]) -> int:
    request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    try:
        out = run(request)
    except SetupError as e:
        out = {"status": "setup_error", "error": str(e)}
    Path(request["result"]).write_text(json.dumps(out), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
