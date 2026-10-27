import io
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from metaskill.evaluate import sandbox as sandbox_module
from metaskill.evaluate.judge import JudgeError
from metaskill.evaluate.sandbox import (
    DEFAULT_IMAGE,
    OUTPUT_TAIL,
    SANDBOX_PATH,
    SKILL_DIR,
    USER,
    WORKDIR,
    DockerSandbox,
    Problem,
    Streamed,
)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["docker"], returncode, stdout, stderr)


class FakeDocker(DockerSandbox):
    """DockerSandbox with Docker replaced. `start` is what `docker run` returns,
    `inspect_image` what `image inspect` does, and `streamed(args, input)` what each
    `docker exec` gives back."""

    def __init__(self, start=None, inspect_image=None, streamed=None):
        super().__init__()
        self.start, self.inspect_image = start or completed(stdout="id"), inspect_image
        self.streamed = streamed or (lambda args, input: Streamed(0, b"", b""))
        self.calls, self.execs = [], []

    def _docker(self, *args):
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            if isinstance(self.inspect_image, Exception):
                raise self.inspect_image
            return self.inspect_image or completed(stdout="sha256:x")
        if args[0] == "run":
            if isinstance(self.start, Exception):
                raise self.start
            return self.start
        return completed()

    def _stream(self, args, input, timeout, stdout_limit=None):
        self.execs.append(SimpleNamespace(args=args, input=input, timeout=timeout,
                                          stdout_limit=stdout_limit))
        return self.streamed(args, input)


def removed(sandbox):
    return [c for c in sandbox.calls if c[:2] == ("rm", "--force")]


def archive(**members):
    """A tar archive: name -> bytes for a file, or ("link", target) for a symlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# --- The container ------------------------------------------------------------


def test_container_is_isolated():
    sandbox = FakeDocker()
    workspace = sandbox.open([])
    [run] = [c for c in sandbox.calls if c[0] == "run"]
    joined = " ".join(run)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--pull never", "--init",
                 "--security-opt no-new-privileges", f"--workdir {WORKDIR}", f"--user {USER}"):
        assert flag in joined
    # The run's folder is a 16 MB volume in memory, holding at most 4096 files; nothing of
    # this machine is mounted.
    [work] = [run[i + 1] for i, a in enumerate(run) if a == "--tmpfs" and
              run[i + 1].startswith(f"{WORKDIR}:")]
    assert {"exec", "size=16m", "nr_inodes=4096", "mode=1777"} <= set(work.split(":")[1].split(","))
    assert "--mount" not in run and "-v" not in run and "--volume" not in run
    # /tmp holds the skill's files, whose scripts may be run.
    [tmp] = [run[i + 1] for i, a in enumerate(run) if a == "--tmpfs" and
             run[i + 1].startswith("/tmp:")]
    assert {"exec", "size=64m"} <= set(tmp.split(":")[1].split(","))
    assert workspace.skill_dir == SKILL_DIR and SKILL_DIR.startswith("/tmp/")
    assert [run[i + 1] for i, a in enumerate(run) if a == "--env"] == [
        f"PATH={SANDBOX_PATH}", "HOME=/tmp"]
    assert run[-2:] == (sandbox.image, "infinity")
    assert workspace.root == WORKDIR and sandbox.execs == []  # no fixtures, nothing copied


def test_image_is_pinned_by_digest():
    assert "@sha256:" in DEFAULT_IMAGE
    assert DockerSandbox().pull_command == f"docker pull {DEFAULT_IMAGE}"


@pytest.mark.parametrize("disk", ["16m,suid", "16", "", 16])
def test_disk_size_cannot_add_mount_options(disk):
    with pytest.raises(ValueError, match="disk"):
        DockerSandbox(disk=disk)


def test_disk_size_can_be_set():
    sandbox = FakeDocker()
    sandbox.disk = "32m"
    sandbox.open([])
    assert "size=32m" in " ".join(next(c for c in sandbox.calls if c[0] == "run"))


def test_fixtures_are_copied_in_as_an_archive(tmp_path):
    (tmp_path / "data.csv").write_text("a,b\n", encoding="utf-8")
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "x.txt").write_text("x", encoding="utf-8")
    sandbox = FakeDocker()
    sandbox.open([tmp_path / "data.csv", tmp_path / "inputs"])
    [put] = sandbox.execs
    assert put.args[:2] == ["exec", "--interactive"]
    assert put.args[-1] == f"mkdir -p {WORKDIR} && tar -x -f - -C {WORKDIR}"
    with tarfile.open(fileobj=io.BytesIO(put.input)) as tar:
        assert sorted(tar.getnames()) == ["data.csv", "inputs", "inputs/x.txt"]


def test_a_container_that_does_not_start_is_a_judge_error():
    sandbox = FakeDocker(start=completed(125, stderr="docker: invalid reference format"))
    with pytest.raises(JudgeError, match="did not start: docker: invalid reference"):
        sandbox.open([])
    assert len(removed(sandbox)) == 1


def test_docker_missing_at_run_time_is_a_judge_error():
    with pytest.raises(JudgeError, match="could not start Docker"):
        FakeDocker(start=FileNotFoundError("docker")).open([])


def test_fixtures_that_do_not_fit_are_a_judge_error_and_the_container_is_removed(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x")
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(
        2, b"", b"tar: big.bin: Cannot write: No space left on device"))
    with pytest.raises(JudgeError, match="No space left on device"):
        sandbox.open([tmp_path / "big.bin"])
    assert len(removed(sandbox)) == 1


def test_close_removes_the_container_and_ignores_docker_failing():
    sandbox = FakeDocker()
    workspace = sandbox.open([])
    workspace.close()
    assert removed(sandbox) == [("rm", "--force", workspace.name)]

    def broken(*args):
        raise OSError("gone")

    sandbox._docker = broken
    workspace.close()  # does not raise


# --- Commands -----------------------------------------------------------------


def test_commands_run_in_the_container_with_a_timeout_inside_it():
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(3, b"out", b"boom"))
    workspace = sandbox.open([])
    result = workspace.run_command("python test.py", 5)
    [run] = sandbox.execs
    assert run.args == ["exec", "--workdir", WORKDIR, workspace.name,
                        "timeout", "-s", "KILL", "5", "sh", "-c", "python test.py"]
    assert run.input is None and run.timeout > 5
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out", "boom")


def test_a_command_that_runs_past_its_timeout_has_no_exit_code(monkeypatch):
    # Docker not answering: the deadline here ran out.
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(None, b"", b"still going"))
    assert sandbox.open([]).run_command("sleep 999", 1).exit_code is None

    # Killed by `timeout` in the container (137), which the clock tells apart from a
    # command exiting 137 itself.
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(sandbox_module.time, "monotonic", lambda: next(clock))
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(137, b"", b""))
    assert sandbox.open([]).run_command("sleep 999", 1).exit_code is None


def test_a_command_exiting_like_docker_is_just_an_exit_code():
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(125, b"", b""))
    assert sandbox.open([]).run_command("exit 125", 5).exit_code == 125


def test_output_is_cut_to_its_end():
    noisy = b"x" * 10 + b"y" * (OUTPUT_TAIL * 2)
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(0, noisy, b""))
    assert sandbox.open([]).run_command("yes", 5).stdout == "y" * OUTPUT_TAIL


def test_stream_keeps_only_the_end_of_endless_output():
    # A real process, our own: what _stream keeps is bounded, whatever is printed.
    sandbox = DockerSandbox(docker=sys.executable)
    result = sandbox._stream(["-I", "-c", "import sys; sys.stdout.write('x' * 10**6 + 'end')"],
                             None, 30)
    assert result.code == 0 and result.stdout.endswith(b"end")
    assert len(result.stdout) <= 4 * OUTPUT_TAIL

    head = sandbox._stream(["-I", "-c", "print('x' * 1000)"], None, 30, stdout_limit=10)
    assert head.stdout == b"x" * 10 and head.overflow


def test_stream_feeds_input_and_times_out():
    sandbox = DockerSandbox(docker=sys.executable)
    echoed = sandbox._stream(["-I", "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                             b"hello", 30)
    assert echoed.stdout == b"hello"
    assert sandbox._stream(["-I", "-c", "import time; time.sleep(30)"], None, 0.5).code is None


# --- Copying the folder out -----------------------------------------------------


def test_export_keeps_files_and_drops_what_leads_out(tmp_path):
    data = archive(**{"report.txt": b"ok", "out/a.txt": b"a",
                      "passwd": ("link", "/etc/passwd"), "../escape.txt": b"x",
                      "up": ("link", "../../outside")})
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(0, data, b""))
    workspace = sandbox.open([])
    workspace.export(tmp_path / "copy")
    [tar] = sandbox.execs
    assert tar.args == ["exec", "--workdir", WORKDIR, workspace.name,
                        "tar", "-c", "-f", "-", "."]
    assert tar.stdout_limit == sandbox_module.EXPORT_LIMIT
    copy = tmp_path / "copy"
    assert (copy / "report.txt").read_bytes() == b"ok"
    assert (copy / "out" / "a.txt").read_bytes() == b"a"
    assert not (copy / "passwd").exists() and not (copy / "up").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_export_limit_grows_with_the_disk_size():
    assert DockerSandbox(disk="16m").export_limit == sandbox_module.EXPORT_LIMIT
    assert DockerSandbox(disk="100m").export_limit == 200 * 1024 * 1024
    assert DockerSandbox(disk="1g").export_limit == 2 * 1024 ** 3


@pytest.mark.parametrize("streamed, message", [
    (Streamed(None, b"", b""), "timed out"),
    (Streamed(0, b"x", b"", overflow=True), "bytes as an archive"),
    (Streamed(0, b"not a tar archive at all" * 50, b""), "could not be copied out"),
])
def test_export_failures_are_judge_errors(tmp_path, streamed, message):
    workspace = FakeDocker(streamed=lambda args, input: streamed).open([])
    with pytest.raises(JudgeError, match=message):
        workspace.export(tmp_path / "copy")


# --- Availability ---------------------------------------------------------------


def test_available_when_the_image_is_there():
    assert FakeDocker().unavailable() == ""


@pytest.mark.parametrize("inspect, reason", [
    (FileNotFoundError("docker"), "Docker is not installed"),
    (subprocess.TimeoutExpired("docker", 30), "Docker did not answer"),
    (completed(1, stderr="Error: No such image: python:3.12-slim"),
     f"run `docker pull {DEFAULT_IMAGE}`"),
    (completed(1, stderr="Cannot connect to the Docker daemon"),
     "Docker is not usable: Cannot connect to the Docker daemon"),
    (PermissionError("docker"), "Docker could not be started"),
])
def test_unavailable_says_why(inspect, reason):
    assert reason in FakeDocker(inspect_image=inspect).unavailable()


@pytest.mark.parametrize("inspect, problem", [
    (FileNotFoundError("docker"), Problem.NOT_INSTALLED),
    (subprocess.TimeoutExpired("docker", 30), Problem.NOT_RUNNING),
    (completed(1, stderr="Error: No such image: x"), Problem.NO_IMAGE),
    (completed(1, stderr="permission denied on docker.sock"),
     Problem.NOT_RUNNING),
    (PermissionError("docker"), Problem.NOT_RUNNING),
])
def test_check_says_what_kind_of_problem(inspect, problem):
    found = FakeDocker(inspect_image=inspect).check()
    assert found is not None and found[0] is problem
    assert FakeDocker().check() is None


# --- Real Docker, where it is installed -------------------------------------------


@pytest.fixture
def docker():
    real = DockerSandbox()
    reason = real.unavailable()
    if reason:
        pytest.skip(reason)
    return real


def test_real_sandbox(docker, tmp_path, monkeypatch):
    (tmp_path / "test.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    workspace = docker.open([tmp_path / "test.py"])
    try:
        assert workspace.run_command("python test.py", 60).exit_code == 3

        # Writes land in the run's folder, which the judges get a copy of; the rest of
        # the system is read-only.
        assert workspace.run_command("echo hi > out.txt", 60).exit_code == 0
        assert workspace.run_command("touch /etc/x", 60).exit_code != 0
        workspace.export(tmp_path / "copy")
        assert (tmp_path / "copy" / "out.txt").read_text() == "hi\n"

        # The folder holds 16 MB at most.
        fill = workspace.run_command("head -c 20000000 /dev/zero > big", 60)
        assert fill.exit_code != 0 and "No space left" in fill.stderr

        # The skill's files go to /tmp/skill, outside the run's folder, and can run.
        (tmp_path / "skill").mkdir()
        (tmp_path / "skill" / "go.sh").write_text("#!/bin/sh\nexit 4\n", encoding="utf-8",
                                                  newline="\n")
        workspace.put([tmp_path / "skill" / "go.sh"], workspace.skill_dir)
        script = f"{workspace.skill_dir}/go.sh"
        assert workspace.run_command(f"chmod +x {script} && {script}", 60).exit_code == 4

        # No network, and none of the host's environment.
        monkeypatch.setenv("METASKILL_SECRET", "leaked")
        assert workspace.run_command('test -z "$METASKILL_SECRET"', 60).exit_code == 0
        no_net = "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""
        assert workspace.run_command(no_net, 60).exit_code != 0

        assert workspace.run_command("sleep 30", 2).exit_code is None
    finally:
        workspace.close()
