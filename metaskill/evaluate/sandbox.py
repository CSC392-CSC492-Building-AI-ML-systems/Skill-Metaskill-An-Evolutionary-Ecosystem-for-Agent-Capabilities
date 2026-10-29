"""Sandbox for agent runs and `exit_code` checks (docs/evaluate/runner.md 2.6).

The skill under test steers the agent, so everything the agent runs is untrusted code,
and so is an `exit_code` check's command (`python test.py`), which runs files the run
could have replaced. Both run in one `Workspace` per run: opened with the task's
fixtures, and removed, with every process still in it, once the run has been judged.
judge.py runs `exit_code` commands only through a `run_command` (judge.md 1.3), and
runner.py passes one that runs them in the run's workspace.

`DockerSandbox` gives each run its own container: no network, a read-only system, a
16 MB tmpfs volume at /work as the run's folder, nothing of the user's, and a clean
environment. Nothing the run writes reaches this machine's disk; the judges read a copy
of /work, made through a filter that drops links leading out of it.

Only the agent's actions run in the sandbox. The agent itself, the model calls, stays
on this machine: it needs the user's provider and credentials, which never go in.
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Protocol

from .judge import JudgeError
from .models import DISK_SIZE

# Pinned by digest, so a tag moved under us cannot change what runs (or how).
# Bump it, from `docker buildx imagetools inspect python:3.12-slim`, for updates.
DEFAULT_IMAGE = ("python:3.12-slim@sha256:"
                 "e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6")
DOCKER_INSTALL_URL = "https://docs.docker.com/get-started/get-docker/"
WORKDIR = "/work"  # the run's folder in the container
# Where the agent puts the skill's own files: in /tmp, so they do not count against
# the run's folder, and are not in it for the judges to read as the run's work.
SKILL_DIR = "/tmp/skill"
# PATH inside the container. It never includes the run's folder, so a `python` the run
# planted there is not the one that runs.
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
USER = "65534:65534"  # nobody: nothing in the container is the user's, so no uid to match
DISK = "16m"  # size of the run's folder
FILES = 4096  # files and folders the run's folder may hold, so its copy stays small too
OUTPUT_TAIL = 2000  # characters of a command's stdout and stderr kept
DOCKER_TIMEOUT = 30.0  # seconds for Docker's own calls (run, inspect, rm, copies)
GRACE = 5.0  # seconds past a command's timeout before `docker exec` itself is killed
# Bytes of /work's archive read, at least: twice the folder's size if that is more.
EXPORT_LIMIT = 64 * 1024 * 1024


@dataclass
class CommandResult:
    """What a command in a workspace did."""

    exit_code: int | None  # None if it timed out
    stdout: str  # the last OUTPUT_TAIL characters
    stderr: str  # the last OUTPUT_TAIL characters


class Workspace(Protocol):
    """One run's sandboxed folder. Every command of the run, the agent's and the
    checks', runs here, in `root`."""

    root: str  # the folder as commands see it, e.g. /work
    skill_dir: str  # where the skill's files go, with put, e.g. /tmp/skill

    def run_command(self, command: str, timeout: float,
                    input: bytes | None = None) -> CommandResult:
        """Run `command` with `sh -c` in root. Never runs anything on this machine."""
        ...

    def put(self, paths: list[Path], folder: str) -> None:
        """Copy files and folders of this machine into `folder` in the workspace, by
        name. Raises JudgeError if they do not fit."""
        ...

    def export(self, dest: Path) -> None:
        """Copy root to dest on this machine, for the judges. Links leading out of it,
        and anything not a file, folder or link, are left out."""
        ...

    def close(self) -> None:
        """Remove the workspace and every process still running in it."""
        ...


class Sandbox(Protocol):
    """Where runner.py runs the agent and `exit_code` commands. Tests use a fake one."""

    def unavailable(self) -> str:
        """Why runs cannot start, or "" if they can. Asked once per runner call."""
        ...

    def open(self, fixtures: list[Path]) -> Workspace:
        """A new workspace with the fixtures copied into its root. Raises JudgeError
        if it cannot be made."""
        ...


class Problem(StrEnum):
    """Why DockerSandbox cannot run, so the install prompt can say what to do
    (dependencies.py)."""

    NOT_INSTALLED = "not-installed"  # no docker executable: the user installs Docker
    NOT_RUNNING = "not-running"  # installed, but Docker does not answer or refuses us
    NO_IMAGE = "no-image"  # Docker works, but the sandbox image is not pulled


class DockerSandbox:
    """Runs each workspace in a new Docker container, removed on close.

    The image must already be pulled (`pull_command`): it is never pulled mid-run,
    which would need the network.
    """

    def __init__(self, image: str = DEFAULT_IMAGE, *, docker: str = "docker",
                 memory: str = "512m", cpus: str = "1", pids: int = 256,
                 disk: str = DISK, files: int = FILES):
        if not isinstance(disk, str) or not DISK_SIZE.fullmatch(disk):
            raise ValueError(f"disk must be a size like 16m, got {disk!r}")
        self.image = image
        self.docker = docker  # the docker executable
        self.memory, self.cpus, self.pids = memory, cpus, pids
        self.disk, self.files = disk, files  # size and file count of the run's folder
        # The archive holds the folder plus a header per file, so twice it is room enough.
        self.export_limit = max(EXPORT_LIMIT, 2 * _bytes(disk))

    @property
    def pull_command(self) -> str:
        return f"docker pull {self.image}"

    def check(self) -> tuple[Problem, str] | None:
        """What stops runs from starting, with a message, or None if nothing does."""
        try:
            result = self._docker("image", "inspect", "--format", "{{.Id}}", self.image)
        except FileNotFoundError:
            return (Problem.NOT_INSTALLED,
                    "Docker is not installed (agent runs and exit_code checks run in a "
                    "Docker container)")
        except subprocess.TimeoutExpired:
            return Problem.NOT_RUNNING, "Docker did not answer"
        except OSError as e:  # e.g. a docker that this user may not run
            return Problem.NOT_RUNNING, f"Docker could not be started: {e}"
        if result.returncode == 0:
            return None
        error = result.stderr.strip()
        if "no such image" in error.lower():
            return (Problem.NO_IMAGE,
                    f"the sandbox image is not pulled; run `{self.pull_command}`")
        # Usually the Docker daemon is not started; on Linux, also a user without access
        # to Docker's socket. Either way Docker's own message says which.
        return Problem.NOT_RUNNING, f"Docker is not usable: {error[-200:]}"

    def unavailable(self) -> str:
        problem = self.check()
        return problem[1] if problem else ""

    def open(self, fixtures: list[Path]) -> DockerWorkspace:
        workspace = DockerWorkspace(self, f"metaskill-run-{uuid.uuid4().hex[:12]}")
        try:
            try:
                result = self._docker(*self._run_args(workspace.name))
            except (OSError, subprocess.TimeoutExpired) as e:
                raise JudgeError(f"could not start Docker: {e}") from e
            if result.returncode != 0:
                raise JudgeError(
                    f"the sandbox container did not start: {result.stderr.strip()[-200:]}")
            workspace.put(fixtures, WORKDIR)
        except BaseException:
            workspace.close()
            raise
        return workspace

    def _run_args(self, name: str) -> list[str]:
        return [
            "run", "--detach", "--name", name, "--pull", "never",
            # tini as PID 1, so processes the run leaves behind are reaped.
            "--init",
            "--network", "none",
            # /tmp holds SKILL_DIR, whose scripts the agent may run, so `exec`.
            "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=64m",
            # The run's folder: a size-limited volume in memory, so the run cannot fill
            # this machine's disk. Docker's default for a tmpfs is noexec; the run may
            # run scripts it wrote, so `exec`.
            "--tmpfs", (f"{WORKDIR}:rw,exec,nosuid,nodev,size={self.disk},"
                        f"nr_inodes={self.files},mode=1777"),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids), "--memory", self.memory, "--cpus", self.cpus,
            "--user", USER, "--workdir", WORKDIR,
            # Docker passes none of the host's environment, so none of Hermes' API keys.
            "--env", f"PATH={SANDBOX_PATH}", "--env", "HOME=/tmp",
            "--entrypoint", "sleep", self.image, "infinity",
        ]

    # The two ways Docker is called; tests replace them.

    def _docker(self, *args: str) -> subprocess.CompletedProcess[str]:
        """A short Docker call with its output in memory: run, inspect, rm."""
        return subprocess.run([self.docker, *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=DOCKER_TIMEOUT)

    def _stream(self, args: list[str], input: bytes | None, timeout: float,
                stdout_limit: int | None = None) -> Streamed:
        """A `docker exec`, whose output the run controls. Only the end of stdout and
        stderr is kept, or with stdout_limit, the start of stdout; nothing goes to
        disk. Returns code None if it ran past timeout."""
        try:
            process = subprocess.Popen(
                [self.docker, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL if input is None else subprocess.PIPE)
        except OSError as e:
            raise JudgeError(f"could not start Docker: {e}") from e
        assert process.stdout is not None and process.stderr is not None
        tail = 4 * OUTPUT_TAIL  # bytes: up to 4 per character
        out = _Buffer(process.stdout, stdout_limit or tail, head=stdout_limit is not None)
        err = _Buffer(process.stderr, tail)
        if input is not None:
            assert process.stdin is not None
            threading.Thread(target=_feed, args=(process.stdin, input), daemon=True).start()
        try:
            code: int | None = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = None
        out.join()
        err.join()
        return Streamed(code, bytes(out.data), bytes(err.data), out.overflow)


class DockerWorkspace:
    """One run's container. Commands reach it with `docker exec`."""

    root = WORKDIR
    skill_dir = SKILL_DIR

    def __init__(self, sandbox: DockerSandbox, name: str):
        self.sandbox, self.name = sandbox, name

    def run_command(self, command: str, timeout: float,
                    input: bytes | None = None) -> CommandResult:
        # `timeout` in the container kills the command there: killing `docker exec` here
        # would leave it running. The deadline here only covers Docker not answering.
        args = ["exec", *(["--interactive"] if input is not None else []),
                "--workdir", WORKDIR, self.name,
                "timeout", "-s", "KILL", f"{timeout:g}", "sh", "-c", command]
        start = time.monotonic()
        result = self.sandbox._stream(args, input, timeout + GRACE)
        code = result.code
        # Killed by `timeout` exits 137, which a command could also exit with; the
        # clock says which it was.
        if code is not None and time.monotonic() - start >= timeout:
            code = None
        return CommandResult(code, _text(result.stdout), _text(result.stderr))

    def put(self, paths: list[Path], folder: str) -> None:
        if not paths:
            return
        result = self.run_command(
            f"mkdir -p {shlex.quote(folder)} && tar -x -f - -C {shlex.quote(folder)}",
            DOCKER_TIMEOUT, input=_archive(paths))
        if result.exit_code != 0:
            raise JudgeError(f"could not copy {', '.join(p.name for p in paths)} into the "
                             f"sandbox: {result.stderr.strip()[-200:]}")

    def export(self, dest: Path) -> None:
        # tar runs in the container and may fail on files the run made unreadable; the
        # rest is still in the archive. What it wrote is trusted only through _keep.
        result = self.sandbox._stream(
            ["exec", "--workdir", WORKDIR, self.name, "tar", "-c", "-f", "-", "."],
            None, DOCKER_TIMEOUT, stdout_limit=self.sandbox.export_limit)
        if result.code is None:
            raise JudgeError("copying the run's folder out of the sandbox timed out")
        if result.overflow:
            raise JudgeError(f"the run's folder is over {self.sandbox.export_limit} bytes "
                             "as an archive")
        try:
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                # A name this system cannot create (e.g. `a:b` on Windows) is skipped
                # rather than fail the copy, which would leave the run unjudged.
                archive.errorlevel = 0
                archive.extractall(dest, filter=_keep)
        except tarfile.TarError as e:
            raise JudgeError(f"the run's folder could not be copied out: {e}") from e

    def close(self) -> None:
        # Removing the container kills every process still in it.
        try:
            self.sandbox._docker("rm", "--force", self.name)
        except (OSError, subprocess.TimeoutExpired):
            pass


@dataclass
class Streamed:
    """What DockerSandbox._stream got back."""

    code: int | None  # None if it timed out
    stdout: bytes
    stderr: bytes
    overflow: bool = False  # stdout was over stdout_limit


class _Buffer:
    """Reads a stream to its end in a thread, keeping at most `size` bytes: the last
    ones, or with `head`, the first ones."""

    def __init__(self, stream: IO[bytes], size: int, head: bool = False):
        self.data, self.size, self.head, self.overflow = bytearray(), size, head, False
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream: IO[bytes]) -> None:
        with stream:
            while chunk := os.read(stream.fileno(), 65536):  # what is there, not 64 KB
                if self.head:
                    room = self.size - len(self.data)
                    self.data += chunk[:room]
                    self.overflow = self.overflow or len(chunk) > room
                else:
                    self.data += chunk
                    del self.data[:-self.size]

    def join(self) -> None:
        self.thread.join(DOCKER_TIMEOUT)


def _feed(stdin: IO[bytes], data: bytes) -> None:
    try:
        with stdin:
            stdin.write(data)
    except (BrokenPipeError, OSError):
        pass  # the command ended without reading it all; its exit code says so


def _archive(paths: list[Path]) -> bytes:
    """paths as a tar archive, each under its own name. Links are kept as links:
    tasks.py has checked that they stay inside the fixtures."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in paths:
            archive.add(path, arcname=path.name)
    return buffer.getvalue()


def _keep(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    """tarfile's `data` filter, skipping what it refuses instead of stopping: absolute
    paths, links out of dest, devices."""
    try:
        return tarfile.data_filter(member, dest)
    except tarfile.FilterError:
        return None


def _bytes(size: str) -> int:
    """A DISK_SIZE, e.g. 16m, in bytes."""
    return int(size[:-1]) * 1024 ** "kmg".index(size[-1].lower()) * 1024


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")[-OUTPUT_TAIL:]
