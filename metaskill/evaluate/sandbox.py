"""Sandbox for the commands of `exit_code` checks (docs/evaluate/runner.md 2.6).

An `exit_code` check runs a command, usually on files in the run's folder (`python
test.py`). The agent run, steered by the skill under test, could have replaced those
files, so the command is untrusted code. judge.py runs it only through a `run_command`
(judge.md 1.3), and runner.py passes the sandbox's.

`DockerSandbox` runs each command in a fresh container: no network, a read-only system,
the run's folder as the only writable mount, nothing of the user's, and a clean
environment. It never runs anything on the host.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import IO, Protocol

from .judge import JudgeError

DEFAULT_IMAGE = "python:3.12-slim"
WORKDIR = "/work"  # where the run's folder is mounted in the container
# PATH inside the container. It never includes the run's folder, so a `python` the run
# planted there is not the one that runs.
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
STDERR_TAIL = 2000  # characters of the command's stderr kept
DOCKER_TIMEOUT = 30.0  # seconds for Docker's own calls (inspect, rm)


class Sandbox(Protocol):
    """Where runner.py has `exit_code` commands run. Tests use a fake one."""

    def unavailable(self) -> str:
        """Why commands cannot run, or "" if they can. Asked once per runner call,
        before the first task with an `exit_code` check."""
        ...

    def run_command(self, command: str, workdir: Path,
                    timeout: float) -> tuple[int | None, str]:
        """judge.CommandRunner: run `command` in `workdir`. Returns the exit code (None
        if it timed out) and the tail of stderr."""
        ...


class DockerSandbox:
    """Runs each command in a new Docker container, removed afterwards.

    The image must already be pulled (`docker pull python:3.12-slim`): it is never
    pulled mid-check, which would need the network and count against the timeout.
    """

    def __init__(self, image: str = DEFAULT_IMAGE, *, docker: str = "docker",
                 memory: str = "512m", cpus: str = "1", pids: int = 256):
        self.image = image
        self.docker = docker  # the docker executable
        self.memory, self.cpus, self.pids = memory, cpus, pids

    def unavailable(self) -> str:
        try:
            result = self._docker("image", "inspect", "--format", "{{.Id}}", self.image)
        except FileNotFoundError:
            return "Docker is not installed (exit_code checks run in a Docker container)"
        except subprocess.TimeoutExpired:
            return "Docker did not answer"
        if result.returncode == 0:
            return ""
        error = result.stderr.strip()
        if "no such image" in error.lower():
            return f"the sandbox image is not pulled; run `docker pull {self.image}`"
        return f"Docker is not usable: {error[-200:]}"

    def run_command(self, command: str, workdir: Path,
                    timeout: float) -> tuple[int | None, str]:
        name = f"metaskill-check-{uuid.uuid4().hex[:12]}"
        # stderr goes to a file outside workdir, not a pipe: a leftover process holding a
        # pipe open would make reading it hang.
        with tempfile.TemporaryFile() as stderr:
            try:
                process = self._start(self._run_args(name, command, workdir), stderr)
            except OSError as e:
                raise JudgeError(f"could not start Docker: {e}") from e
            try:
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    return None, _tail(stderr)
                return self._exit_code(name, stderr), _tail(stderr)
            finally:
                # Removing the container kills every process still in it.
                self._remove(name)
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def _run_args(self, name: str, command: str, workdir: Path) -> list[str]:
        source = str(Path(workdir).resolve())
        if "," in source:  # --mount separates its fields with commas
            raise JudgeError(f"the run folder {source} cannot be mounted: it contains a comma")
        args = [
            self.docker, "run", "--name", name, "--pull", "never",
            # tini as PID 1: when the command ends the container stops, and every
            # background process it left behind is killed with it.
            "--init",
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids), "--memory", self.memory, "--cpus", self.cpus,
            "--mount", f"type=bind,source={source},target={WORKDIR}", "--workdir", WORKDIR,
            # Docker passes none of the host's environment, so none of Hermes' API keys.
            "--env", f"PATH={SANDBOX_PATH}", "--env", "HOME=/tmp",
            "--entrypoint", "sh",
        ]
        if hasattr(os, "getuid"):
            # Files the command writes stay the user's, so the runner can delete the
            # folder. Docker Desktop (Windows, macOS) maps ownership itself.
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return args + [self.image, "-c", command]

    def _exit_code(self, name: str, stderr: IO[bytes]) -> int:
        """The command's exit code, read from Docker rather than from `docker run`, whose
        own failures (125) a command could otherwise imitate to leave itself unjudged."""
        result = self._docker("container", "inspect", "--format", "{{json .State}}", name)
        if result.returncode != 0:
            raise JudgeError(f"the sandbox container did not start: {_tail(stderr)[-200:]}")
        state = json.loads(result.stdout)
        if state.get("Error"):
            raise JudgeError(f"the sandbox failed: {state['Error']}")
        return int(state["ExitCode"])

    def _remove(self, name: str) -> None:
        try:
            self._docker("rm", "--force", name)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # The two ways Docker is called; tests replace them.

    def _docker(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.docker, *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=DOCKER_TIMEOUT)

    def _start(self, args: list[str], stderr: IO[bytes]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=stderr)


def _tail(file: IO[bytes]) -> str:
    """The last STDERR_TAIL characters written to file, read without loading it all."""
    file.flush()
    size = file.seek(0, os.SEEK_END)
    file.seek(max(0, size - 4 * STDERR_TAIL))
    return file.read().decode("utf-8", errors="replace")[-STDERR_TAIL:]
