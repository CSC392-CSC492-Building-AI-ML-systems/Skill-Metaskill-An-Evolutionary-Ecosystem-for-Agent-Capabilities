"""HermesAgent: the runner's Agent as a real Hermes session (docs/evaluate/hermes_agent.md).

Each run starts hermes_child.py in a new process on Hermes' own interpreter and bootstrap,
taken from `hermes --print-runtime-command`. The session's model calls run on this machine
with the user's Hermes provider; its terminal and file tools run in the run's sandbox
container. This module never imports Hermes.

Outcomes:
- The session finished (completed or not): an AgentRun, which the runner judges.
- The agent raised mid-session, or ran out of time: an exception, which the runner records
  as a failed run (the agent failing is an outcome of the task, runner.md 2.2).
- Hermes or the sandbox backend could not be set up, or the model provider could not serve
  the session (Hermes marks it failed: model unavailable, billing, retries exhausted, a
  content-policy block): HermesAgentUnavailable, a JudgeError, which the runner records as
  an error. Scoring such a run as failed would blame the skill for this machine.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .judge import JudgeError, clean
from .models import Skill
from .runner import AgentRun, Workspace, agent_run_from_hermes_usage
from .sandbox import SKILL_DIR

CHILD = Path(__file__).with_name("hermes_child.py")
PROTOCOL = 1  # hermes_child.PROTOCOL
# How `hermes --print-runtime-command`'s bootstrap ends; replaced to run the child instead.
_HERMES_MAIN = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
_STDERR_TAIL = 1500
_RUNTIME_TIMEOUT = 60  # seconds for `hermes --print-runtime-command`


class HermesAgentUnavailable(JudgeError):
    """Hermes, the sandbox backend or the model provider failed the run: it was not set
    up, or the provider could not serve it. Either way it tells nothing about the skill."""


@dataclass
class HermesAgent:
    """An Agent (runner.py) backed by Hermes. One instance serves every run of a call."""

    model: str | None = None      # None: the user's configured Hermes model
    provider: str | None = None   # None: the model's configured provider
    max_iterations: int = 30      # model turns per session
    timeout: float = 600.0        # seconds for a whole session
    hermes: str = "hermes"        # the Hermes launcher
    _argv: list[str] | None = field(default=None, init=False, repr=False)

    def run(self, prompt: str, skill: Skill | None, workspace: Workspace) -> AgentRun:
        container, docker = _container(workspace)
        argv = self._runtime_argv()
        with tempfile.TemporaryDirectory(prefix="metaskill-agent-") as tmp:
            request_path, result_path = Path(tmp) / "request.json", Path(tmp) / "result.json"
            request = {
                "protocol": PROTOCOL, "result": str(result_path), "prompt": prompt,
                "skill_message": skill_message(skill, workspace) if skill else None,
                "docker": docker, "container": container, "workdir": workspace.root,
                "model": self.model, "provider": self.provider,
                "max_iterations": self.max_iterations,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            code, stderr = _run_child([*argv, str(request_path)], self.timeout)
            if not result_path.exists():
                raise HermesAgentUnavailable(
                    f"the Hermes session process exited {code} without a result: "
                    f"{clean(stderr[-_STDERR_TAIL:], _STDERR_TAIL)}")
            return _agent_run(_read_result(result_path))

    def check(self, workspace: Workspace) -> dict[str, Any]:
        """Without a model: set Hermes up for `workspace` and drive its real terminal and
        file tools once each. Returns what they returned; raises HermesAgentUnavailable
        if the setup refuses. For integration tests and a pre-flight check."""
        container, docker = _container(workspace)
        with tempfile.TemporaryDirectory(prefix="metaskill-agent-") as tmp:
            result_path = Path(tmp) / "result.json"
            request = {"protocol": PROTOCOL, "result": str(result_path), "check": True,
                       "docker": docker, "container": container, "workdir": workspace.root}
            (Path(tmp) / "request.json").write_text(json.dumps(request), encoding="utf-8")
            code, stderr = _run_child([*self._runtime_argv(), str(Path(tmp) / "request.json")],
                                      self.timeout)
            if not result_path.exists():
                raise HermesAgentUnavailable(
                    f"the check exited {code}: {clean(stderr[-_STDERR_TAIL:], _STDERR_TAIL)}")
            result = _read_result(result_path)
            if result.get("status") != "ok":
                raise HermesAgentUnavailable(str(result.get("error")))
            return result

    def _runtime_argv(self) -> list[str]:
        """Hermes' launch argv with its main replaced by the child, so the child gets
        exactly Hermes' interpreter, isolation flags, sys.path, HERMES_HOME and bootstrap."""
        if self._argv is None:
            try:
                proc = subprocess.run([self.hermes, "--print-runtime-command"], stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=_RUNTIME_TIMEOUT)
                argv = json.loads(proc.stdout)
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                raise HermesAgentUnavailable(
                    f"could not read Hermes' runtime from `{self.hermes} --print-runtime-command`: "
                    f"{type(e).__name__}") from e
            if (not isinstance(argv, list) or len(argv) < 3 or argv[-2] != "-c"
                    or not all(isinstance(a, str) for a in argv)
                    or not argv[-1].endswith(_HERMES_MAIN)):
                raise HermesAgentUnavailable(
                    "Hermes' runtime command has an unexpected shape; this Hermes version is "
                    "not supported by the evaluate agent (docs/evaluate/hermes_agent.md)")
            code = argv[-1][:-len(_HERMES_MAIN)] + f"runpy.run_path({str(CHILD)!r}, run_name='__main__')"
            self._argv = [*argv[:-1], code]
        return self._argv


def skill_message(skill: Skill, workspace: Workspace | None = None) -> str:
    """How Hermes preloads a skill (`hermes -s`, agent/skill_commands._build_skill_message),
    pointing at the sandbox copy instead of the host's skills folder: Hermes' own message
    names a host path the sandbox does not have, and its skill_view tool reads the host.
    Bundled files are listed as the runner copied them; links are left out (runner.md 2.2)."""
    skill_dir = workspace.skill_dir if workspace is not None else SKILL_DIR
    parts = [
        f'[IMPORTANT: The user launched this CLI session with the "{skill.name}" skill '
        "preloaded. Treat its instructions as active guidance for the duration of this "
        "session unless the user overrides them.]",
        "", skill.body.strip(), "", f"[Skill directory: {skill_dir}]",
        "Resolve any relative paths in this skill (e.g. `scripts/foo.js`, "
        "`templates/config.yaml`) against that directory, then run them with the terminal "
        "tool using the absolute path.",
    ]
    files = [f.as_posix() for f in skill.files if not (Path(skill.path) / f).is_symlink()]
    if files:
        parts += ["", "[This skill has supporting files (paths relative to the skill directory above):]"]
        parts += [f"- {f}" for f in files]
        parts.append(f"\nRead any of these with the file tools, or run scripts directly by "
                     f"absolute path (e.g. `{skill_dir}/scripts/foo.sh`).")
    return "\n".join(parts)


def _container(workspace: Workspace) -> tuple[str, str]:
    """The container and docker executable behind a DockerWorkspace."""
    name = getattr(workspace, "name", None)
    docker = getattr(getattr(workspace, "sandbox", None), "docker", None)
    if not isinstance(name, str) or not name or not isinstance(docker, str) or not docker:
        raise HermesAgentUnavailable(
            f"HermesAgent needs a Docker sandbox workspace, got {type(workspace).__name__}")
    return name, docker


def _run_child(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """Run the child in its own process group, so a timeout stops it and every `docker exec`
    it started; the commands themselves stop with the container."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, errors="replace",
                            start_new_session=True)
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise TimeoutError(f"the Hermes session ran past {timeout:g} s") from None
    return proc.returncode, stderr or ""


def _read_result(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HermesAgentUnavailable(f"the Hermes session wrote an unreadable result: "
                                     f"{type(e).__name__}") from e
    if not isinstance(result, dict):
        raise HermesAgentUnavailable("the Hermes session wrote a result that is not an object")
    return result


def _agent_run(result: dict[str, Any]) -> AgentRun:
    status = result.get("status")
    if status == "setup_error":
        raise HermesAgentUnavailable(str(result.get("error") or "Hermes setup failed"))
    if status == "provider_error":
        reason = result.get("failure_reason")
        raise HermesAgentUnavailable(
            f"the model provider could not serve the session"
            f"{f' ({reason})' if reason else ''}: {result.get('error') or 'no detail'}")
    if status == "agent_error":
        reason = result.get("failure_reason")
        raise RuntimeError(f"the agent's session failed{f' ({reason})' if reason else ''}: "
                           f"{result.get('error') or 'the agent failed'}")
    if status != "ok":
        raise HermesAgentUnavailable(f"unexpected result status {status!r} from the Hermes session")
    run = agent_run_from_hermes_usage(result.get("final_response") or "", result.get("usage") or {},
                                      transcript=result.get("transcript") or "")
    tool_calls = result.get("tool_calls")
    return dataclasses.replace(run, tool_calls=tool_calls if isinstance(tool_calls, int) else None)
