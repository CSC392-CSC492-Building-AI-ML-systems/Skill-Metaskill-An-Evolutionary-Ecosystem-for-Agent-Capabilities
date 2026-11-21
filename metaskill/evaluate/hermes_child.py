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
# Hermes marks a session `failed` for two different kinds of reasons. Most say the environment
# could not serve it (billing, auth, rate limits, a model the provider does not have, a
# provider outage, Hermes' own runtime errors): the run tells nothing about the skill and is
# not scored. These say how the task itself went, and the skill can drive them (a skill that
# floods the context, or whose text the provider blocks): the run fails like any other.
# Anything not named here stays an environment problem, so a new Hermes reason is never
# scored against a skill by accident.
TASK_FAILURES = frozenset({
    "context_overflow", "truncated", "content_policy_blocked",
    "context_compression_exhausted", "context_compression_timeout",
})
WINDOWS_MIN_HERMES = (0, 23)  # the first Hermes whose backends can name their default_cwd
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


def windows_version_problem(version: str, platform: str) -> str | None:
    """Why this Hermes cannot run on `platform`, or None. On Windows the backend's working
    folder needs Hermes 0.23 or later (its `default_cwd`; hermes_agent.md 4). A version that
    cannot be read is let through: the pre-flight check still refuses tools that miss /work."""
    if platform != "win32":
        return None
    def leading_int(text: str) -> int:  # "22rc1" -> 22, "23+381" -> 23
        digits = ""
        for c in text:
            if not c.isdigit():
                break
            digits += c
        return int(digits)

    parts = version.split(".")
    try:
        found = (leading_int(parts[0]), leading_int(parts[1]))
    except (IndexError, ValueError):
        return None
    if found == (0, 0):  # an install stamp without version info (_hermes_version), not a version
        return None
    if found < WINDOWS_MIN_HERMES:
        need = ".".join(map(str, WINDOWS_MIN_HERMES))
        return f"Hermes {version} is too old for Windows: the evaluate agent needs {need} or later there"
    return None


def failure_status(result: dict[str, Any]) -> str:
    """`agent_error` (a failed run) when Hermes' failure_reason or turn_exit_reason is one of
    TASK_FAILURES, else `provider_error` (the environment failed; the run is not scored)."""
    reasons = {result.get("failure_reason"), result.get("turn_exit_reason")}
    return "agent_error" if reasons & TASK_FAILURES else "provider_error"


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

        # Hermes' file tools (read_file, write_file, patch, search_files) work on the host's
        # files directly when uses_host_filesystem is True, and through these commands when
        # it is False. False is BaseEnvironment's default today; it is set here so a Hermes
        # that changed the default could not route the agent's file tools to this machine.
        uses_host_filesystem = False
        is_local = False

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

        @property
        def default_cwd(self) -> str:
            # Hermes drops a TERMINAL_CWD that os.path.isabs rejects and uses the backend's
            # default, "/root" unless the backend names one. On Windows, Python 3.13 and later
            # (Hermes ships 3.14) do not count "/work" as absolute, so without this every tool
            # ran in /root there. As the default, the workdir is kept as is.
            return workdir

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


class _ModelChoice:
    """The model and provider to use, and an alias's own base URL and key if it has them."""

    def __init__(self, model: str, provider: str | None):
        self.model, self.provider = model, provider
        self.base_url: str | None = None
        self.api_key: str | None = None


def _configured_model(model_cfg: object) -> str:
    if isinstance(model_cfg, str):
        return model_cfg
    raw = model_cfg.get("default") or model_cfg.get("model") or ""
    if isinstance(raw, dict):
        from hermes_cli.config import split_model_config_default

        return split_model_config_default(raw)[0]
    return str(raw or "")


def _resolve_model_and_provider(cfg: dict, model: str | None, provider: str | None) -> _ModelChoice:
    """The model and provider a session uses: the request's, else the user's configured ones.

    Hermes 0.21 kept this in hermes_cli/oneshot.py for `hermes -z`; 0.22 moved that run behind
    the gateway and removed it, so it is kept here, on the helpers that are still in
    hermes_cli.model_switch and hermes_cli.models. Auto-detection of the provider only runs when
    a model was named (as `/model <name>` does), because the configured provider may not host it.
    """
    model_cfg = cfg.get("model") or {}
    explicit_model = (model or "").strip() or os.getenv("HERMES_INFERENCE_MODEL", "").strip()
    choice = _ModelChoice(explicit_model or _configured_model(model_cfg),
                          (provider or "").strip() or None)
    if choice.provider is not None or not explicit_model:
        return choice

    from hermes_cli import model_switch as ms
    from hermes_cli.models import detect_provider_for_model
    try:
        ms._ensure_direct_aliases()
        direct = ms.DIRECT_ALIASES.get(explicit_model.strip().lower())
    except Exception:
        direct = None
    if direct is None:
        cfg_provider = ""
        if isinstance(model_cfg, dict):
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        current = cfg_provider or os.getenv("HERMES_INFERENCE_PROVIDER", "").strip().lower() or "auto"
        route = ms.resolve_startup_model_route(
            explicit_model, current_provider=current,
            user_providers=cfg.get("providers"), custom_providers=cfg.get("custom_providers"))
        if route is not None:
            choice.provider, choice.model = route.provider, route.model
            return choice
        detected = detect_provider_for_model(explicit_model, current)
        if detected:
            choice.provider, choice.model = detected
        return choice

    choice.model, choice.provider = direct.model, direct.provider
    # The alias's own resolver, so a URL-bearing alias never carries another provider's key.
    try:
        choice.provider, choice.api_key = ms.direct_alias_runtime_request(direct)
    except Exception:
        choice.api_key = None
    if direct.base_url:
        choice.base_url = direct.base_url.rstrip("/")
    return choice


def _build_agent(request: dict[str, Any]):
    """An AIAgent built as Hermes builds one for a single turn, with only the terminal and
    file tools, no context files, no memory and no saved session."""
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_with_fallback
    from run_agent import AIAgent

    _turn_off_tool_search()
    cfg = load_config()
    choice = _resolve_model_and_provider(cfg, request.get("model"), request.get("provider"))
    runtime, _ = resolve_runtime_with_fallback(
        cfg, requested=choice.provider, target_model=choice.model or None,
        explicit_base_url=choice.base_url, explicit_api_key=choice.api_key)
    agent = AIAgent(
        api_key=runtime.get("api_key"), base_url=runtime.get("base_url"),
        provider=runtime.get("provider"), requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"), model=choice.model,
        credential_pool=runtime.get("credential_pool"),
        request_overrides=runtime.get("request_overrides"),
        enabled_toolsets=list(TOOLSETS), quiet_mode=True, platform="batch",
        skip_context_files=True, skip_memory=True,
        max_iterations=int(request["max_iterations"]),
        ephemeral_system_prompt=request.get("skill_message") or None)
    agent.suppress_status_output = True
    seal_agent(agent)
    _check_tools(agent)
    return agent, choice.model, runtime.get("provider")


def seal_agent(agent) -> None:
    """Nothing of an evaluation session may outlive it. After a turn, Hermes can fork the agent
    to review it and write memories or skills straight into the user's stores, and mirror the
    turn into an external memory provider; the session runs a skill under evaluation, so it
    could plant either. Today both are off only indirectly (no skill_manage tool, skip_memory),
    so this turns the review off by its own switch and refuses an agent with a memory provider."""
    agent.skip_background_review = True
    if getattr(agent, "_memory_manager", None) is not None:
        raise SetupError("the Hermes agent has a memory provider attached: a skill under "
                         "evaluation could write to the user's memory")


def _turn_off_tool_search() -> None:
    """Hermes puts a tool-search bridge (tool_search, tool_describe, tool_call) in front of the
    tools. It also reaches the user's connected apps (mail, calendars, issue trackers) and can
    install MCP servers, none of which runs in the sandbox. Turn it off for this process, so the
    agent gets its tools directly. A Hermes without the module has nothing to turn off, and
    _check_tools refuses any tool this does not account for."""
    try:
        from tools import tool_search
    except ImportError:
        return
    off = tool_search.ToolSearchConfig.from_raw({"enabled": "off"})
    tool_search.load_config = lambda: off
    tool_search.load_config_readonly = lambda: off


def _check_tools(agent) -> None:
    """The agent has exactly the tools of the terminal and file toolsets, so everything it can
    do goes through the sandbox backend. Anything else (a connector, an MCP or plugin tool, a
    bridge) means Hermes added a way out that this does not know about: nothing runs."""
    from toolsets import resolve_toolset

    allowed = {name for toolset in TOOLSETS for name in resolve_toolset(toolset)}
    extra = unexpected_tools(agent.tools, allowed)
    if extra:
        raise SetupError("the Hermes agent has tools beyond terminal and file, which could "
                         f"reach outside the sandbox: {', '.join(extra)[:300]}")


def unexpected_tools(tool_defs: Any, allowed: set[str]) -> list[str]:
    """Names in an agent's tool definitions that are not in `allowed`, sorted. A definition
    whose name cannot be read counts as unexpected, so a changed shape fails closed."""
    extra = set()
    for definition in tool_defs or []:
        function = definition.get("function") if isinstance(definition, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or name not in allowed:
            extra.add(str(name))
    return sorted(extra)


def _hermes_version() -> str:
    # 0.22 and later keep the version in version_info; their __version__ is an install stamp
    # that reads "0.0.0" without one.
    try:
        from hermes_cli.version_info import get_version_info
        return str(get_version_info().derived_version)
    except Exception:
        pass
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
    version = _hermes_version()
    problem = windows_version_problem(version, sys.platform)
    if problem:
        raise SetupError(problem)
    _setup(request)
    out: dict[str, Any] = {"hermes_version": version}
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
        # Hermes puts its own error text in final_response for a failed session. That text is
        # not the agent's answer and must not be judged as one. Whether the failure counts
        # against the skill depends on why it failed (failure_status).
        status = failure_status(result)
        out.update(status=status,
                   error=str(result.get("error") or "the session failed"),
                   failure_reason=result.get("failure_reason") or result.get("turn_exit_reason"))
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
