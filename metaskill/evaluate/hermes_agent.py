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

import contextvars
import dataclasses
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .judge import JudgeError, clean
from .models import DEFAULT_AGENT_MAX_ITERATIONS, DEFAULT_AGENT_TIMEOUT, Skill
from .runner import AgentRun, Workspace, agent_run_from_hermes_usage, copied
from .sandbox import SKILL_DIR

CHILD = Path(__file__).with_name("hermes_child.py")
PROTOCOL = 1  # hermes_child.PROTOCOL
# How `hermes --print-runtime-command`'s bootstrap ends; replaced to run the child instead.
_HERMES_MAIN = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
_STDERR_TAIL = 1500
_RUNTIME_TIMEOUT = 60  # seconds for `hermes --print-runtime-command`
_TASKKILL_TIMEOUT = 30  # seconds for `taskkill` to stop a timed-out session on Windows
_CREATE_SUSPENDED = 0x00000004  # winjob.CREATE_SUSPENDED; winjob is imported on Windows only
_REAP_TIMEOUT = 5  # seconds to reap a child stopped by an interrupt
WARM_FLAG = "--warm"  # hermes_child.WARM: import Hermes, then wait for the request on stdin
# Seconds between starting two warm children. Each spends ~2 s of a CPU importing Hermes, and
# more when they import together. Measured with 16 on a 28-thread Windows machine (12% idle):
# all at once peaked at 61% total CPU; 0.25 s apart at 51%; 0.5 s apart at 33%, the 16 started
# in 8 s. Sessions still start as soon as a child is (HermesAgent._take).
WARM_STAGGER = 0.5
# The longest a session waits for the next warm child (HermesAgent._take) before it starts a
# cold one. A bound only: the filler hands one out every WARM_STAGGER seconds.
_TAKE_WAIT = 10.0
_log = logging.getLogger(__name__)


class HermesAgentUnavailable(JudgeError):
    """Hermes, the sandbox backend or the model provider failed the run: it was not set
    up, or the provider could not serve it. Either way it tells nothing about the skill.

    `stderr` is the tail of the Hermes process's error output when it died without a
    result; it can hold this machine's paths, so check() keeps it out of the message.
    `rate_limited` is True when the provider throttled the session (RATE_LIMIT_REASONS):
    the runner then waits and runs it again (backoff.py)."""

    def __init__(self, message: str, stderr: str = "", rate_limited: bool = False):
        super().__init__(message)
        self.stderr = stderr
        self.rate_limited = rate_limited


# Hermes' failure_reason for a provider that throttled the session: worth waiting and
# trying again, unlike billing, auth or a model the provider does not have.
RATE_LIMIT_REASONS = frozenset({"rate_limit", "overloaded"})


@dataclass
class HermesAgent:
    """An Agent (runner.py) backed by Hermes. One instance serves every run of a call."""

    model: str | None = None      # None: the user's configured Hermes model
    provider: str | None = None   # None: the model's configured provider
    max_iterations: int = DEFAULT_AGENT_MAX_ITERATIONS  # model turns per session
    timeout: float = DEFAULT_AGENT_TIMEOUT  # seconds for a whole session
    hermes: str = "hermes"        # the Hermes launcher
    # Keep Hermes children started ahead of their sessions (prewarm), so a session does not
    # wait the ~2 s Hermes takes to import. False: every session starts its own, cold.
    warm: bool = True
    stagger: float = WARM_STAGGER  # seconds between starting two warm children
    # The models sessions actually ran on, as Hermes resolved them (for the report header).
    models_used: set[str] = field(default_factory=set, init=False)
    _argv: list[str] | None = field(default=None, init=False, repr=False)
    # The runner runs sessions in parallel threads (runner.md 2.7): the state above, and the
    # Hermes processes running now (for cancel), are kept under this lock.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False,
                                  compare=False)
    # The runtime command is read once, by a `hermes` process of its own: under a lock of its
    # own, so cancel() and a session ending (_track) never wait for that process.
    _argv_lock: threading.Lock = field(default_factory=threading.Lock, init=False,
                                       repr=False, compare=False)
    # Each running Hermes process, with its Job Object on Windows (None elsewhere), so
    # cancel() stops its whole tree as a timeout does (_kill_tree).
    _children: dict[subprocess.Popen, Any] = field(default_factory=dict, init=False,
                                                   repr=False, compare=False)
    _cancelled: bool = field(default=False, init=False, repr=False, compare=False)
    # Warm children waiting for a session (prewarm), how many are being started, and how many
    # to keep: the most sessions the runner runs at once.
    _idle: list[_Warm] = field(default_factory=list, init=False, repr=False, compare=False)
    _starting: int = field(default=0, init=False, repr=False, compare=False)
    _target: int = field(default=0, init=False, repr=False, compare=False)
    # Whether the filler thread (_fill) is starting children, one every `stagger` seconds,
    # and the condition, on `_lock`, that tells sessions waiting for one (_take) and the
    # filler (woken from its stagger by close() and cancel()) that the pool changed.
    _filling: bool = field(default=False, init=False, repr=False, compare=False)
    _filler: threading.Thread | None = field(default=None, init=False, repr=False,
                                             compare=False)
    _changed: threading.Condition = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._changed = threading.Condition(self._lock)

    def run(self, prompt: str, skill: Skill | None, workspace: Workspace) -> AgentRun:
        if self._cancelled:
            raise HermesAgentUnavailable("the evaluation was stopped before this session")
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
            warm = self._take()
            if warm is not None:
                code, stderr = _run_child(argv, self.timeout, track=self._track, warm=warm,
                                          request=str(request_path))
            else:
                code, stderr = _run_child([*argv, str(request_path)], self.timeout,
                                          track=self._track)
            if not result_path.exists():
                raise HermesAgentUnavailable(
                    f"the Hermes session process exited {code} without a result: "
                    f"{clean(stderr[-_STDERR_TAIL:], _STDERR_TAIL)}")
            result = _read_result(result_path)
            if isinstance(result.get("model"), str) and result["model"]:
                with self._lock:
                    self.models_used.add(result["model"])
            return _agent_run(result)

    def cancel(self) -> None:
        """Stop every session running now, and refuse new ones. The runner calls this when
        an evaluation is interrupted or fails (runner.md 2.7), so no Hermes process goes on
        calling the model after its container is gone."""
        with self._lock:
            self._cancelled = True
            running = list(self._children.items())
            idle, self._idle = self._idle, []
            self._changed.notify_all()  # the filler stops; waiting sessions stop waiting
        for proc, job in running:
            _kill(proc, job)
        for warm in idle:
            warm.discard()

    def prewarm(self, sessions: int) -> None:
        """Keep `sessions` Hermes children started and waiting (the runner passes how many
        sessions it runs at once), each having imported Hermes, so a session hands its
        request to one rather than starting Hermes itself. Never raises: without them,
        sessions start cold as before. One child serves one session; a replacement starts
        whenever one is taken. close() stops those still waiting.

        Returns at once: a filler thread starts them `stagger` seconds apart, so their
        imports do not all load the CPU together (WARM_STAGGER)."""
        if not self.warm or sessions < 1:
            return
        with self._lock:
            self._target = max(self._target, sessions)
        self._refill()

    def close(self) -> None:
        """Stop the warm children still waiting, and start no more until prewarm(). Running
        sessions are not touched. evaluate calls this when it ends."""
        with self._lock:
            self._target = 0
            idle, self._idle = self._idle, []
            self._changed.notify_all()  # the filler stops; waiting sessions stop waiting
        for warm in idle:
            warm.discard()

    def _take(self) -> _Warm | None:
        """A warm child still alive, or None (start one cold); its replacement is started.

        With none waiting while the filler is still starting them, wait for its next one
        rather than start cold: a cold start imports Hermes as a warm one does, so sessions
        that all started cold at once would load the CPU together, which the stagger is
        there to prevent. A child just started is no slower than a cold one, and the filler
        hands out one every `stagger` seconds; _TAKE_WAIT only bounds a stuck filler."""
        dead, taken = [], None
        deadline = time.monotonic() + _TAKE_WAIT
        with self._lock:
            while True:
                while self._idle:
                    warm = self._idle.pop(0)
                    if warm.proc.poll() is None:
                        taken = warm
                        break
                    dead.append(warm)
                left = deadline - time.monotonic()
                if taken is not None or self._cancelled or not self._filling or left <= 0:
                    break
                self._changed.wait(left)
        for warm in dead:  # exited on its own: idle too long, or Hermes would not import
            warm.discard()
        self._refill()  # a replacement for the one taken, or for those found dead
        return taken

    def _refill(self) -> None:
        """Have the filler thread start warm children until there are `_target` of them,
        waiting or starting. Never raises; it starts the thread if none is running."""
        with self._lock:
            if self._cancelled or self._filling or not self._short():
                return
            self._filling = True
        try:
            # Its own copy of the caller's context, as every thread here has (threads.py).
            filler = threading.Thread(target=contextvars.copy_context().run, args=(self._fill,),
                                      name="metaskill-prewarm", daemon=True)
            filler.start()
        except Exception:  # no thread to spare: sessions start cold
            _log.debug("metaskill: the warm Hermes filler could not start", exc_info=True)
            with self._lock:
                self._filling = False
                self._changed.notify_all()
            return
        self._filler = filler

    def _short(self) -> bool:
        """Fewer warm children waiting or starting than `_target`. Under `_lock`."""
        return len(self._idle) + self._starting < self._target

    def _fill(self) -> None:
        """The filler thread: start a warm child, wait `stagger` seconds, and so on until the
        pool is full, close() empties its target or cancel() stops it. It clears `_filling`
        in the same locked step that finds there is nothing to do, so a take right after
        never finds it set by a filler that has ended."""
        ended = False
        try:
            argv = [*self._runtime_argv(), WARM_FLAG]
            first = True
            while True:
                with self._lock:
                    if not first and self.stagger > 0 and not self._cancelled:
                        self._changed.wait(self.stagger)  # cut short by close() or cancel()
                    first = False
                    if self._cancelled or not self._short():
                        ended = self._stop_filling()
                        return
                    self._starting += 1
                warm, keep = None, False
                try:
                    warm = _Warm.start(argv)
                except Exception:  # Windows refusing: sessions start cold
                    _log.debug("metaskill: a warm Hermes child could not be started",
                               exc_info=True)
                finally:
                    # Also on an interrupt, or the count would block every later refill.
                    with self._lock:
                        self._starting -= 1
                        keep = warm is not None and not self._cancelled and self._target > 0
                        if keep:
                            self._idle.append(warm)
                            self._changed.notify_all()  # a session may be waiting for one
                        else:
                            ended = self._stop_filling()
                if not keep:
                    if warm is not None:
                        warm.discard()
                    return
        except Exception:  # no Hermes runtime command: sessions start cold, and say why
            _log.debug("metaskill: no warm Hermes children", exc_info=True)
        finally:
            if not ended:
                with self._lock:
                    self._stop_filling()

    def _stop_filling(self) -> bool:
        """Mark the filler ended and wake the sessions waiting on it. Under `_lock`."""
        self._filling = False
        self._changed.notify_all()
        return True

    def resume(self) -> None:
        """Take sessions again after cancel(). The runner calls this when a new runner call
        starts (one per skill of a rank call), so a stop that the caller recovered from does
        not refuse every later skill's sessions."""
        with self._lock:
            self._cancelled = False

    def _track(self, proc: subprocess.Popen, running: bool, job: Any = None) -> None:
        """Record a Hermes process (and its Job Object on Windows) as running, or as ended;
        one started after cancel() is stopped at once."""
        with self._lock:
            if running:
                self._children[proc] = job
            else:
                self._children.pop(proc, None)
            stop = running and self._cancelled
        if stop:
            _kill(proc, job)

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
                                      self.timeout, track=self._track)
            if not result_path.exists():
                raise HermesAgentUnavailable(f"the Hermes check exited {code} without a result",
                                             stderr=clean(stderr[-_STDERR_TAIL:], _STDERR_TAIL))
            result = _read_result(result_path)
            if result.get("status") != "ok":
                raise HermesAgentUnavailable(str(result.get("error")))
            # Setup being accepted is not enough: with Docker stopped, or on a Hermes whose
            # tools run in another folder, every tool call fails and setup still succeeds.
            problems = check_problems(result.get("check"), workspace.root)
            if problems:
                raise HermesAgentUnavailable(
                    "the Hermes tools did not work in the sandbox: " + "; ".join(problems))
            return result

    def _runtime_argv(self) -> list[str]:
        """Hermes' launch argv with its main replaced by the child, so the child gets
        exactly Hermes' interpreter, isolation flags, sys.path, HERMES_HOME and bootstrap."""
        with self._argv_lock:
            return self._runtime_argv_locked()

    def _runtime_argv_locked(self) -> list[str]:
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
    Bundled files are listed as the runner copied them (runner.copied: links, hard links and
    paths outside the skill are left out, runner.md 2.2)."""
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
    files = [f.as_posix() for f in skill.files if copied(skill, f)]
    if files:
        parts += ["", "[This skill has supporting files (paths relative to the skill directory above):]"]
        parts += [f"- {f}" for f in files]
        parts.append(f"\nRead any of these with the file tools, or run scripts directly by "
                     f"absolute path (e.g. `{skill_dir}/scripts/foo.sh`).")
    return "\n".join(parts)


def _windows() -> bool:
    return sys.platform == "win32"


def check_problems(check: Any, workdir: str) -> list[str]:
    """What is wrong in the result of the child's check mode, one short reason each; an empty
    list means the file tool wrote into `workdir`, the terminal ran there as exit 0, the file
    tool read back what the terminal wrote, and every guard refused. Each tool's answer is
    JSON; one that cannot be read counts as failed."""
    if not isinstance(check, dict):
        return ["no check result"]
    problems = []

    def answer(tool: str) -> dict[str, Any] | None:
        """The tool's parsed answer, or None (already reported) when it cannot be read. An
        empty object is a readable answer: it is checked for the success fields like any other."""
        try:
            value = json.loads(check.get(tool))
        except (TypeError, ValueError):
            value = None
        if not isinstance(value, dict):
            problems.append(f"{tool} gave no readable answer")
            return None
        if value.get("error"):
            problems.append(f"{tool} reported an error")
        return value

    written = answer("write_file")
    if written is not None and written.get("resolved_path") != f"{workdir}/check.txt":
        problems.append(f"write_file did not write into {workdir}")
    ran = answer("terminal")
    if ran is not None and (ran.get("exit_code") != 0 or not str(ran.get("output")).startswith(f"{workdir}\n")):
        problems.append(f"terminal did not run in {workdir} and exit 0")
    read = answer("read_file")
    if read is not None and (read.get("not_found") or "from-terminal" not in str(read.get("content"))):
        problems.append("read_file did not find what the terminal wrote")
    guards = check.get("guards")
    if (not isinstance(guards, dict) or not guards
            or not all(isinstance(v, str) and v.startswith("refused") for v in guards.values())):
        problems.append("a guard did not refuse")
    return problems


def _container(workspace: Workspace) -> tuple[str, str]:
    """The container and docker executable behind a DockerWorkspace."""
    name = getattr(workspace, "name", None)
    docker = getattr(getattr(workspace, "sandbox", None), "docker", None)
    if not isinstance(name, str) or not name or not isinstance(docker, str) or not docker:
        raise HermesAgentUnavailable(
            f"HermesAgent needs a Docker sandbox workspace, got {type(workspace).__name__}")
    return name, docker


def _group() -> dict[str, Any]:
    """Popen arguments for a child of its own: a session (POSIX), or a new process group that
    starts suspended until it is in its Job Object (Windows, _contain)."""
    return ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED}
            if _windows() else {"start_new_session": True})


@dataclass
class _Warm:
    """A Hermes child started ahead of its session (HermesAgent.prewarm): it has imported
    Hermes and waits for its request file's path on stdin. Its stderr goes to a file, not a
    pipe: nobody reads a pipe while it waits, and a full one would block it."""

    proc: subprocess.Popen
    job: Any
    stderr: Any  # a temporary file

    @classmethod
    def start(cls, argv: list[str]) -> _Warm:
        stderr = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=stderr, text=True, errors="replace", **_group())
        except BaseException:
            stderr.close()
            raise
        try:
            job = _contain(proc) if _windows() else None
        except BaseException:
            _kill(proc)
            stderr.close()
            raise
        return cls(proc, job, stderr)

    def tail(self) -> str:
        try:
            self.stderr.seek(0, os.SEEK_END)
            size = self.stderr.tell()
            self.stderr.seek(max(0, size - 4 * _STDERR_TAIL))
            return self.stderr.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def discard(self) -> None:
        """Stop it unused (the evaluation ended or stopped), then reap it. It has run no
        session, so it started nothing: its Job Object (Windows) or process group (POSIX)
        holds it all, and `taskkill /T` is not needed. That takes 0.3 s per child, and
        close() stops up to max_parallel_runs of them before evaluate returns. One that has
        already exited is left alone: on POSIX its pid may be another process's by now."""
        if self.proc.poll() is None:
            if self.job is not None and self.job.kill():
                pass
            elif _windows():
                self.proc.kill()
            else:
                _kill(self.proc)
        try:
            self.proc.communicate(timeout=_REAP_TIMEOUT)
        except BaseException:
            pass
        self.finish()

    def finish(self) -> None:
        if self.job is not None:
            self.job.close()
        try:
            self.stderr.close()
        except OSError:
            pass


def _run_child(argv: list[str], timeout: float,
               track: Callable[[subprocess.Popen, bool, Any], None] | None = None,
               warm: _Warm | None = None, request: str | None = None,
               ) -> tuple[int | None, str]:
    """Run the child so that a timeout stops it and every `docker exec` it started; the
    commands themselves stop with the container. Stopping only the child is not enough:
    communicate() would wait on the `docker exec` processes, which hold its stderr open.

    POSIX: a session (process group) of its own. Windows: a Job Object (winjob.py), with
    `taskkill /T` if Windows refuses one. The child starts suspended and runs only once it
    is in the job, so nothing it starts can be outside it.

    Interrupted (Ctrl-C, or the runner stopping), the tree is stopped too, or it would go on
    calling the model. `track` is told when the child starts (with its job) and ends, for
    HermesAgent.cancel.

    With `warm`, the child was started ahead (HermesAgent.prewarm) and is already in its
    job: it is given `request`, the request file's path, on stdin, and the timeout counts
    from then."""
    if warm is None:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, errors="replace", **_group())
        job = None
    else:
        proc, job = warm.proc, warm.job
    try:
        # Inside the try: an interrupt right after the start still stops the child. On
        # Windows it is suspended until it is in its job (_contain resumes it).
        if warm is None and _windows():
            job = _contain(proc)
        if track is not None:
            track(proc, True, job)
        if warm is None:
            _, stderr = proc.communicate(timeout=timeout)
        else:
            proc.communicate(input=f"{request}\n", timeout=timeout)
            stderr = warm.tail()
    except subprocess.TimeoutExpired:
        _kill_tree(proc, job)
        proc.communicate()
        raise TimeoutError(f"the Hermes session ran past {timeout:g} s") from None
    except BaseException:
        _kill(proc, job)
        # Reaped, and its stderr pipe closed, so no zombie or open pipe is left per stopped
        # session; briefly, as a second Ctrl-C must still get out.
        try:
            proc.communicate(timeout=_REAP_TIMEOUT)
        except BaseException:
            pass  # the first interrupt is the one raised
        raise
    finally:
        if track is not None:
            track(proc, False, job)
        if warm is not None:
            warm.finish()  # closes its job, as below, and its stderr file
        elif job is not None:
            job.close()  # kills anything the child left running, as a session's end should
    return proc.returncode, stderr or ""


def _contain(proc: subprocess.Popen):
    """Put the suspended `proc` in a new Job Object and let it run. Returns the job, or None
    if Windows refuses one (taskkill is then used). Raises HermesAgentUnavailable, with the
    child killed, if it cannot be resumed: it would never run."""
    from . import winjob

    try:
        job = winjob.contain(proc.pid)
    except OSError:
        job = None
    try:
        winjob.resume(proc.pid)
    except OSError as e:
        if job is not None:
            job.close()
        proc.kill()
        proc.communicate()
        raise HermesAgentUnavailable(f"the Hermes session process could not be started: {e}") from e
    return job


def _kill_tree(proc: subprocess.Popen, job=None) -> None:
    """Kill `proc` and every process it started. POSIX: its process group. Windows: both
    ways there are, as each misses what the other finds:
    - `taskkill /T` walks parent links from the child (still running here). It misses a
      process whose parent has already exited.
    - The job holds everything started inside it, orphans too. It misses a process Windows
      starts on the caller's behalf outside it, e.g. a Microsoft Store Python's interpreter,
      which app activation starts.
    taskkill goes first, while the tree's links are whole. If both fail, the child alone."""
    if not _windows():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=_TASKKILL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if job is not None:
        job.kill()
    if proc.poll() is None:
        proc.kill()


def _kill(proc: subprocess.Popen, job: Any = None) -> None:
    """_kill_tree for cleanup (an interrupt, HermesAgent.cancel): never raises, as the
    process may already be gone or no longer ours to signal. A POSIX child already reaped
    (its session ended as cancel() came) is left alone: its pid, the group's id, is free for
    another process. On Windows the pid stays ours while proc holds its handle."""
    if not _windows() and getattr(proc, "returncode", None) is not None:
        return
    try:
        _kill_tree(proc, job)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # already gone


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
            f"{f' ({reason})' if reason else ''}: {result.get('error') or 'no detail'}",
            rate_limited=isinstance(reason, str) and reason in RATE_LIMIT_REASONS)
    if status == "agent_error":
        reason = result.get("failure_reason")
        raise RuntimeError(f"the agent's session failed{f' ({reason})' if reason else ''}: "
                           f"{result.get('error') or 'the agent failed'}")
    if status != "ok":
        raise HermesAgentUnavailable(f"unexpected result status {status!r} from the Hermes session")
    run = agent_run_from_hermes_usage(result.get("final_response") or "", result.get("usage") or {},
                                      transcript=result.get("transcript") or "")
    tool_calls, seconds = result.get("tool_calls"), result.get("seconds")
    return dataclasses.replace(
        run, tool_calls=tool_calls if isinstance(tool_calls, int) else None,
        seconds=float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None)
