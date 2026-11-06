"""Sandbox for agent runs and `exit_code` checks (docs/evaluate/runner.md 2.6).

The skill under test steers the agent, so everything the agent runs is untrusted code,
and so is an `exit_code` check's command (`python test.py`), which runs files the run
could have replaced. Both run in one `Workspace` per run: opened with the task's
fixtures, and removed, with every process still in it, once the run has been judged.
judge.py runs `exit_code` commands only through a `run_command` (judge.md 1.3), and
runner.py passes one that runs them in the run's workspace.

`DockerSandbox` gives each run its own container: no network, a read-only system, a
16 MB tmpfs volume at /work as the run's folder, nothing of the user's, and a clean
environment. The judges read a copy of /work, made through a filter that drops links
that could lead out of it; nothing else the run writes reaches this machine's disk.

Only the agent's actions run in the sandbox. The agent itself, the model calls, stays
on this machine: it needs the user's provider and credentials, which never go in.
"""

from __future__ import annotations

import io
import math
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import IO, Protocol

from .judge import JudgeError
from .models import DISK_SIZE, SANDBOX_MEMORY, size_bytes

# Pinned by digest, so a tag moved under us cannot change what runs (or how).
# Bump it, from `docker buildx imagetools inspect python:3.12-slim`, for updates.
DEFAULT_IMAGE = ("python:3.12-slim@sha256:"
                 "e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6")
DOCKER_INSTALL_URL = "https://docs.docker.com/get-started/get-docker/"
PYTHON_INSTALL_URL = "https://www.python.org/downloads/"
WORKDIR = "/work"  # the run's folder in the container
# Where the agent puts the skill's own files: in /tmp, so they do not count against
# the run's folder, and are not in it for the judges to read as the run's work.
SKILL_DIR = "/tmp/skill"
# PATH inside the container. It never includes the run's folder, so a `python` the run
# planted there is not the one that runs.
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
USER = "65534:65534"  # nobody: nothing in the container is the user's, so no uid to match
DISK = "16m"  # size of the run's folder
TMP = "64m"  # size of /tmp, where the skill's files go
FILES = 4096  # files and folders the run's folder may hold, so its copy stays small too
OUTPUT_TAIL = 2000  # characters of a command's stdout and stderr kept
DOCKER_TIMEOUT = 30.0  # seconds for Docker's own calls (run, inspect, rm, copies)
# Seconds past a command's timeout before `docker exec` itself is killed. Only for Docker
# not answering, so it can be as generous as Docker's other calls: a slow `docker exec`
# must not make a command that finished look timed out.
GRACE = DOCKER_TIMEOUT
# Bytes of /work's archive read, at least: twice the folder's size if that is more.
EXPORT_LIMIT = 64 * 1024 * 1024
# On every container, with when it started, so that one left behind can be found.
STARTED_LABEL = "metaskill.started"
# Seconds before such a container counts as left behind. Long, since a younger one may
# be a run of another evaluation, still going.
ORPHAN_AGE = 24 * 60 * 60
KILLED = 137  # how `timeout -s KILL` exits, as does a command that was killed by itself
# What `timeout --verbose` prints as it kills. A command could print this too, but that
# only fails it, which exiting 1 would as well.
TIMEOUT_NOTICE = b"timeout: sending signal KILL to command"
# How `tar -x` says its folder is full: a write that failed, or one cut short.
TAR_FULL = re.compile(r"No space left|Wrote only \d+ of \d+ bytes")


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
                    input: bytes | IO[bytes] | None = None) -> CommandResult:
        """Run `command` with `sh -c` in root. Never runs anything on this machine.
        Raises RunFailed if the workspace is gone, and JudgeError if Docker is."""
        ...

    def put(self, paths: list[Path], folder: str) -> None:
        """Copy files and folders of this machine into `folder` in the workspace, by
        name. Raises JudgeError if they do not fit, or RunFailed if what does not fit is
        the skill."""
        ...

    def export(self, dest: Path) -> None:
        """Copy root to dest on this machine, for the judges. Links that are absolute or
        go up a folder, and anything not a file, folder or link, are left out. Raises
        RunFailed if the run made the copy impossible, and JudgeError if Docker did."""
        ...

    def close(self) -> None:
        """Remove the workspace and every process still running in it."""
        ...


class RunFailed(JudgeError):
    """The run broke its own workspace: it stopped the container, left a folder that
    cannot be copied out, or came with a skill too large to copy in. Unlike other
    JudgeErrors this is the run's doing, so the run fails rather than being left out, or
    a skill could leave itself unscored."""


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
    OLD_PYTHON = "old-python"  # this Python's tarfile cannot be trusted with a run's files


class DockerSandbox:
    """Runs each workspace in a new Docker container, removed on close.

    The image must already be pulled (`pull_command`): it is never pulled mid-run,
    which would need the network.
    """

    def __init__(self, image: str = DEFAULT_IMAGE, *, docker: str = "docker",
                 memory: str = SANDBOX_MEMORY, cpus: str = "1", pids: int = 256,
                 disk: str = DISK, files: int = FILES):
        if not isinstance(disk, str) or not DISK_SIZE.fullmatch(disk):
            raise ValueError(f"disk must be a size like 16m, got {disk!r}")
        if not isinstance(memory, str) or not DISK_SIZE.fullmatch(memory):
            raise ValueError(f"memory must be a size like 512m, got {memory!r}")
        if size_bytes(disk) >= size_bytes(memory):
            # The folder is in memory, so a larger one could never be filled.
            raise ValueError(f"disk must be under the memory limit of {memory}, got {disk}")
        self.image = image
        self.docker = docker  # the docker executable
        self.memory, self.cpus, self.pids = memory, cpus, pids
        self.disk, self.files = disk, files  # size and file count of the run's folder
        # The archive holds the folder plus a header per file, so twice it is room enough.
        self.export_limit = max(EXPORT_LIMIT, 2 * size_bytes(disk))

    @property
    def pull_command(self) -> str:
        return f"docker pull {self.image}"

    def check(self) -> tuple[Problem, str] | None:
        """What stops runs from starting, with a message, or None if nothing does."""
        if not _safe_tarfile(sys.version_info[:3]):
            return (Problem.OLD_PYTHON,
                    f"Python {sys.version.split()[0]} is too old: a run could write outside "
                    "the folder its files are copied to (CVE-2025-4517). evaluate needs "
                    "3.12.11, 3.13.4 or later")
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
        if problem:
            return problem[1]
        self.sweep()
        return ""

    def sweep(self) -> None:
        """Remove containers past ORPHAN_AGE: what a crash, or `docker rm` failing, left
        running. Only those with our label, and only as a courtesy: a failure here does
        not stop anything."""
        try:
            listed = self._docker(
                "ps", "--all", "--filter", f"label={STARTED_LABEL}",
                "--format", f'{{{{.ID}}}} {{{{.Label "{STARTED_LABEL}"}}}}')
            oldest = time.time() - ORPHAN_AGE
            stale = [fields[0] for fields in map(str.split, listed.stdout.splitlines())
                     if len(fields) == 2 and fields[1].isdecimal()
                     and int(fields[1]) < oldest]
            if stale:
                self._docker("rm", "--force", *stale)
        except (OSError, subprocess.TimeoutExpired):
            pass

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
            "--label", f"{STARTED_LABEL}={int(time.time())}",
            # tini as PID 1, so processes the run leaves behind are reaped.
            "--init",
            "--network", "none",
            # /tmp holds SKILL_DIR, whose scripts the agent may run, so `exec`.
            "--read-only", "--tmpfs", f"/tmp:rw,exec,nosuid,nodev,size={TMP}",
            # The run's folder: a size-limited volume in memory, so the run cannot fill
            # this machine's disk. Docker's default for a tmpfs is noexec; the run may
            # run scripts it wrote, so `exec`.
            "--tmpfs", (f"{WORKDIR}:rw,exec,nosuid,nodev,size={self.disk},"
                        f"nr_inodes={self.files},mode=1777"),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids), "--cpus", self.cpus,
            # Equal to --memory, or Docker adds as much swap again on top of the limit.
            "--memory", self.memory, "--memory-swap", self.memory,
            "--user", USER, "--workdir", WORKDIR,
            # Docker passes none of the host's environment, so none of Hermes' API keys.
            "--env", f"PATH={SANDBOX_PATH}", "--env", "HOME=/tmp",
            "--entrypoint", "sleep", self.image, "infinity",
        ]

    # The two ways Docker is called; tests replace them.

    def _docker(self, *args: str) -> subprocess.CompletedProcess[str]:
        """A short Docker call with its output in memory: run, inspect, rm."""
        return subprocess.run([self.docker, *args], stdin=subprocess.DEVNULL,
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=DOCKER_TIMEOUT)

    def _stream(self, args: list[str], input: bytes | IO[bytes] | None, timeout: float,
                stdout_file: IO[bytes] | None = None,
                stdout_limit: int = 0) -> Streamed:
        """A `docker exec`, whose output the run controls. Only the end of stdout and
        stderr is kept in memory. With stdout_file, stdout goes there instead, and the
        command is killed once it is over stdout_limit. `input` may be an open file,
        read from where it stands. Returns code None if it ran past timeout."""
        stdin: int | IO[bytes] = (subprocess.DEVNULL if input is None else
                                  subprocess.PIPE if isinstance(input, bytes) else input)
        try:
            process = subprocess.Popen(
                [self.docker, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=stdin)
        except OSError as e:
            raise JudgeError(f"could not start Docker: {e}") from e
        assert process.stdout is not None and process.stderr is not None
        tail = 4 * OUTPUT_TAIL  # bytes: up to 4 per character
        out = _Buffer(process.stdout, stdout_limit if stdout_file else tail, stdout_file,
                      process.kill)
        err = _Buffer(process.stderr, tail)
        if isinstance(input, bytes):
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
        if out.error is not None:
            raise JudgeError(f"could not keep the sandbox's output: {out.error}")
        return Streamed(code, bytes(out.data), bytes(err.data), out.overflow)


class DockerWorkspace:
    """One run's container. Commands reach it with `docker exec`."""

    root = WORKDIR
    skill_dir = SKILL_DIR

    def __init__(self, sandbox: DockerSandbox, name: str):
        self.sandbox, self.name = sandbox, name

    def run_command(self, command: str, timeout: float,
                    input: bytes | IO[bytes] | None = None) -> CommandResult:
        # `timeout` in the container kills the command there: killing `docker exec` here
        # would leave it running. The deadline here only covers Docker not answering.
        args = ["exec", *(["--interactive"] if input is not None else []),
                "--workdir", WORKDIR, self.name, "timeout", "--verbose", "-s", "KILL",
                _duration(timeout), "sh", "-c", command]
        result = self.sandbox._stream(args, input, timeout + GRACE)
        code = result.code
        # `docker exec` on a stopped container fails with a code of its own, which a
        # check could be expecting.
        if code != 0 and not self._running():
            raise RunFailed("the run stopped its sandbox container")
        # Killed by `timeout` exits 137, as does a command that was killed by itself. The
        # notice says which, as a clock here cannot: it also counts `docker exec` starting
        # and ending, which the run can slow, and need not agree with the container's.
        if code == KILLED and TIMEOUT_NOTICE in result.stderr:
            code = None
        return CommandResult(code, _text(result.stdout), _text(result.stderr))

    def put(self, paths: list[Path], folder: str) -> None:
        if not paths:
            return
        names = ", ".join(p.name for p in paths)
        # Checked here, as only the container's own limit would stop them otherwise, once
        # all of it had been read. Fixtures too large are the task's fault, so an error; a
        # skill too large is the skill's. Its file names are left out of that message, as
        # they are the skill's to choose and it goes into the report.
        in_root = PurePosixPath(folder).is_relative_to(WORKDIR)
        limit = size_bytes(self.sandbox.disk if in_root else TMP)
        size = _size(paths)
        if size > limit:
            if not in_root:
                raise RunFailed(f"the skill is too large for the sandbox: {size} bytes in "
                                f"one folder, over the {limit} bytes it may use")
            raise JudgeError(f"could not copy {names} into the sandbox: {size} bytes, over "
                             f"the {limit} bytes {folder} can hold")
        with tempfile.TemporaryFile() as spool:  # on disk: not all of it in memory
            _archive(paths, spool)
            spool.seek(0)
            result = self.run_command(
                f"mkdir -p {shlex.quote(folder)} && tar -x -f - -C {shlex.quote(folder)}",
                DOCKER_TIMEOUT, input=spool)
        if result.exit_code != 0:
            # Each of a skill's folders passes above, and together they can still not fit.
            if not in_root and TAR_FULL.search(result.stderr):
                raise RunFailed("the skill is too large for the sandbox: its files are "
                                f"over the {limit} bytes it may use")
            raise JudgeError(f"could not copy {names} into the sandbox: "
                             f"{result.stderr.strip()[-200:]}")

    def export(self, dest: Path) -> None:
        # tar runs in the container and may fail on files the run made unreadable; the
        # rest is still in the archive. What it wrote is trusted only through _keep.
        limit = self.sandbox.export_limit
        with tempfile.TemporaryFile() as spool:  # on disk: `disk` can be large
            result = self.sandbox._stream(
                ["exec", "--workdir", WORKDIR, self.name, "tar", "-c", "-f", "-", "."],
                None, DOCKER_TIMEOUT, stdout_file=spool, stdout_limit=limit)
            if result.overflow:  # e.g. a sparse file: over the limit, yet no disk used
                raise RunFailed(f"the run's folder is over {limit} bytes as an archive")
            if result.code is None:
                raise self._copy_failed("copying the run's folder out of the sandbox "
                                        "timed out")
            spool.seek(0)
            try:
                # r: so that only a plain tar is read, never decompressed.
                with tarfile.open(fileobj=spool, mode="r:") as archive:
                    # A name this system cannot create (e.g. `a:b` on Windows) is skipped
                    # rather than fail the copy, which would leave the run unjudged.
                    archive.errorlevel = 0
                    archive.extractall(dest, filter=_keep)
            except tarfile.TarError as e:
                raise self._copy_failed("the run's folder could not be copied out") from e

    def _copy_failed(self, message: str) -> RunFailed:
        """The failure of a copy Docker could have made: the run's, as it can stop the
        container or tar. Raises JudgeError if Docker has stopped answering instead."""
        self._running()
        return RunFailed(message)

    def _running(self) -> bool:
        """Whether the container is up. Raises JudgeError if Docker does not say, which
        is Docker failing, not the run."""
        try:
            result = self.sandbox._docker("container", "inspect", "--format",
                                          "{{.State.Running}}", self.name)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise JudgeError(f"Docker stopped answering: {e}") from e
        if result.returncode != 0:
            raise JudgeError(f"Docker stopped answering: {result.stderr.strip()[-200:]}")
        return result.stdout.strip() == "true"

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
    overflow: bool = False  # stdout was over stdout_limit, so the command was killed


class _Buffer:
    """Reads a stream to its end in a thread, keeping its last `size` bytes in `data`.
    With `file`, writes it there instead, and calls `stop` once it is over `size`."""

    def __init__(self, stream: IO[bytes], size: int, file: IO[bytes] | None = None,
                 stop: Callable[[], None] = lambda: None):
        self.data, self.size, self.file, self.stop = bytearray(), size, file, stop
        self.overflow, self.written = False, 0
        self.error: OSError | None = None  # writing to file failed
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream: IO[bytes]) -> None:
        with stream:
            while chunk := os.read(stream.fileno(), 65536):  # what is there, not 64 KB
                if self.file is None:
                    self.data += chunk
                    del self.data[:-self.size]
                    continue
                self.written += len(chunk)
                self.overflow = self.written > self.size
                try:
                    if not self.overflow:
                        self.file.write(chunk)
                except OSError as e:
                    self.error = e
                if self.overflow or self.error:
                    self.stop()
                    return

    def join(self) -> None:
        self.thread.join(DOCKER_TIMEOUT)


def _feed(stdin: IO[bytes], data: bytes) -> None:
    try:
        with stdin:
            stdin.write(data)
    except (BrokenPipeError, OSError):
        pass  # the command ended without reading it all; its exit code says so


def _archive(paths: list[Path], file: IO[bytes]) -> None:
    """paths as a tar archive in file, each under its own name. Links are kept as links,
    never followed, so nothing outside paths goes in; tasks.py has checked that those in
    fixtures lead to files that come along."""
    with tarfile.open(fileobj=file, mode="w") as archive:
        for path in paths:
            archive.add(path, arcname=path.name)


def _size(paths: list[Path]) -> int:
    """Bytes in the files of paths, folders included. Links are not followed, as they
    are not by _archive."""
    entries = (entry for path in paths
               for entry in [path, *(path.rglob("*") if path.is_dir() else [])])
    infos = [entry.lstat() for entry in entries]
    return sum(info.st_size for info in infos if stat.S_ISREG(info.st_mode))


def _keep(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    """tarfile's `data` filter, skipping what it refuses instead of stopping: absolute
    paths, links out of dest, devices. ValueError: a name like `a:b`, which Windows reads
    as another drive. OSError must be caught here too: with errorlevel 0, tarfile would
    go on to extract the member unfiltered.

    A link that goes up a folder is skipped too, even one that stays in dest. The filter
    can be led out of dest by a chain of those on older Pythons (CVE-2025-4517), which
    DockerSandbox.check refuses; this holds whatever the filter's next such bug is, since
    the run writes the links and links that only go down cannot leave dest.
    """
    if (member.issym() or member.islnk()) and ".." in re.split(r"[/\\]", member.linkname):
        return None
    try:
        return tarfile.data_filter(member, dest)
    except (tarfile.FilterError, OSError, ValueError):
        return None


def _safe_tarfile(version: tuple[int, int, int]) -> bool:
    """Whether Python `version` has the fixes to tarfile's filters (CVE-2025-4517 and
    others), without which a run's links can lead extraction out of its folder."""
    return version >= (3, 13, 4) or (3, 12, 11) <= version < (3, 13)


def _duration(seconds: float) -> str:
    """seconds as `timeout` reads them, in plain digits. Never 0, which means no limit,
    and exact to the millisecond, where `{:g}` keeps six digits."""
    if not 0 < seconds < math.inf:
        raise ValueError(f"timeout must be a positive number of seconds, got {seconds}")
    return f"{max(seconds, 0.001):.3f}".rstrip("0").rstrip(".")


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")[-OUTPUT_TAIL:]
