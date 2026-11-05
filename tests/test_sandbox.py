import io
import subprocess
import sys
import tarfile
import tempfile
import time
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
    STARTED_LABEL,
    TIMEOUT_NOTICE,
    DockerSandbox,
    Problem,
    RunFailed,
    Streamed,
)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["docker"], returncode, stdout, stderr)


class FakeDocker(DockerSandbox):
    """DockerSandbox with Docker replaced. `start` is what `docker run` returns,
    `inspect_image` what `image inspect` does, `inspect_container` what `container
    inspect` does, and `streamed(args, input)` what each `docker exec` gives back."""

    def __init__(self, start=None, inspect_image=None, streamed=None, inspect_container=None):
        super().__init__()
        self.start, self.inspect_image = start or completed(stdout="id"), inspect_image
        self.inspect_container = inspect_container or completed(stdout="true\n")
        self.listed = completed()  # what `docker ps` gives
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
        if args[0] == "ps":
            return self.listed
        if args[:2] == ("container", "inspect"):
            if isinstance(self.inspect_container, Exception):
                raise self.inspect_container
            return self.inspect_container
        return completed()

    def _stream(self, args, input, timeout, stdout_file=None, stdout_limit=0):
        if hasattr(input, "read"):
            input = input.read()
        self.execs.append(SimpleNamespace(args=args, input=input, timeout=timeout,
                                          stdout_limit=stdout_limit))
        result = self.streamed(args, input)
        if stdout_file is not None:
            stdout_file.write(result.stdout)
        return result


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
                 "--security-opt no-new-privileges", f"--workdir {WORKDIR}", f"--user {USER}",
                 "--memory 512m --memory-swap 512m"):
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


@pytest.mark.parametrize("disk, memory", [("512m", "512m"), ("1g", "512m"), ("2g", "1g")])
def test_a_folder_that_could_never_be_filled_is_refused(disk, memory):
    with pytest.raises(ValueError, match="under the memory limit"):
        DockerSandbox(disk=disk, memory=memory)


@pytest.mark.parametrize("memory", ["512m --privileged", "512", "", 512])
def test_memory_must_be_a_plain_size(memory):
    with pytest.raises(ValueError, match="memory"):
        DockerSandbox(memory=memory)


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


def test_files_over_the_limit_are_refused_before_any_is_read(tmp_path, monkeypatch):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "a.bin").write_bytes(b"x" * 1024)
    (tmp_path / "files" / "b.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(sandbox_module, "_archive", lambda *args: pytest.fail("was read"))
    sandbox = FakeDocker()
    sandbox.disk = "1k"
    with pytest.raises(JudgeError, match="2048 bytes, over the 1024 bytes"):
        sandbox.open([tmp_path / "files"])
    assert sandbox.execs == [] and len(removed(sandbox)) == 1


def test_skill_files_are_held_to_the_size_of_tmp(tmp_path, monkeypatch):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    monkeypatch.setattr(sandbox_module, "TMP", "1k")
    workspace = FakeDocker().open([])
    with pytest.raises(RunFailed, match="skill is too large.* over the 1024 bytes") as caught:
        workspace.put([tmp_path / "big.bin"], SKILL_DIR)
    assert "big.bin" not in str(caught.value)  # the skill's to choose, and it is reported


@pytest.mark.parametrize("message", [b"tar: a.bin: Cannot write: No space left on device",
                                     b"tar: a.bin: Wrote only 3584 of 10240 bytes"])
def test_a_skill_that_only_fills_up_in_the_container_is_too_large_too(tmp_path, message):
    # Each of its folders is under the limit, so only tar finds out.
    (tmp_path / "a.bin").write_bytes(b"x")
    full = Streamed(2, b"", message)
    workspace = FakeDocker().open([])
    workspace.sandbox.streamed = lambda args, input: full
    with pytest.raises(RunFailed, match="skill is too large"):
        workspace.put([tmp_path / "a.bin"], SKILL_DIR)


def test_a_skill_failing_to_copy_for_another_reason_is_an_error(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x")
    workspace = FakeDocker().open([])
    workspace.sandbox.streamed = lambda args, input: Streamed(2, b"", b"tar: broken")
    with pytest.raises(JudgeError, match="tar: broken") as caught:
        workspace.put([tmp_path / "a.bin"], SKILL_DIR)
    assert not isinstance(caught.value, RunFailed)


def test_fixtures_too_large_are_an_error_not_a_failed_run(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    sandbox = FakeDocker()
    sandbox.disk = "1k"
    with pytest.raises(JudgeError) as caught:
        sandbox.open([tmp_path / "big.bin"])
    assert not isinstance(caught.value, RunFailed)


def test_files_are_spooled_to_disk_not_kept_in_memory(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    seen = []
    sandbox = FakeDocker()
    real = sandbox._stream

    def stream(args, input, timeout, **kwargs):
        seen.append(input)
        return real(args, input, timeout, **kwargs)

    sandbox._stream = stream
    sandbox.open([tmp_path / "a.txt"])
    assert not isinstance(seen[0], bytes) and seen[0].closed  # a file, gone afterwards


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
                        "timeout", "--verbose", "-s", "KILL", "5", "sh", "-c",
                        "python test.py"]
    assert run.input is None and run.timeout > 5
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out", "boom")


def timed(command, timeout, streamed):
    return FakeDocker(streamed=lambda args, input: streamed).open([]).run_command(
        command, timeout).exit_code


def test_a_command_that_runs_past_its_timeout_has_no_exit_code():
    # Killed by `timeout` in the container, which says so, or Docker not answering.
    notice = TIMEOUT_NOTICE + " 'sh'\n".encode()
    assert timed("sleep 999", 1, Streamed(137, b"", notice)) is None
    assert timed("sleep 999", 1, Streamed(137, b"", b"noise\n" + notice + b"more")) is None
    assert timed("sleep 999", 1, Streamed(None, b"", b"still going")) is None


def test_a_command_exiting_137_by_itself_keeps_its_exit_code():
    assert timed("kill -9 $$", 5, Streamed(137, b"", b"Killed")) == 137


@pytest.mark.parametrize("code", [0, 3, 137])
def test_exec_latency_does_not_turn_a_finished_command_into_a_timeout(code):
    # `docker exec` taking longer than the whole timeout, as it does under load.
    def slow(args, input):
        time.sleep(0.05)
        return Streamed(code, b"", b"")

    workspace = FakeDocker(streamed=slow).open([])
    assert workspace.run_command("python test.py", 0.01).exit_code == code


@pytest.mark.parametrize("seconds, written", [
    (5, "5"), (2.5, "2.5"), (0.25, "0.25"), (1e6, "1000000"), (1234567, "1234567"),
    (1e-5, "0.001"),  # rounded up: `timeout 0` would mean no limit at all
])
def test_timeouts_are_written_in_plain_digits(seconds, written):
    sandbox = FakeDocker()
    sandbox.open([]).run_command("true", seconds)
    args = sandbox.execs[0].args
    assert args[args.index("KILL") + 1] == written


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_a_timeout_that_is_no_limit_is_refused(seconds):
    with pytest.raises(ValueError, match="positive number of seconds"):
        FakeDocker().open([]).run_command("true", seconds)


@pytest.mark.parametrize("code", [1, 125, 137, None])
def test_a_stopped_container_is_a_failed_run_whatever_docker_exits(code):
    # An exit_code check expecting 1 must not pass on `docker exec` itself exiting 1.
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(code, b"", b"not running"),
                         inspect_container=completed(stdout="false\n"))
    with pytest.raises(RunFailed, match="stopped its sandbox"):
        sandbox.open([]).run_command("exit 1", 5)


@pytest.mark.parametrize("inspect", [
    completed(1, stderr="Cannot connect to the Docker daemon"),
    subprocess.TimeoutExpired("docker", 30),
    OSError("gone"),
])
def test_docker_not_answering_is_an_error_not_a_failed_run(inspect):
    sandbox = FakeDocker(streamed=lambda args, input: Streamed(None, b"", b""),
                         inspect_container=inspect)
    with pytest.raises(JudgeError, match="Docker stopped answering") as caught:
        sandbox.open([]).run_command("true", 5)
    assert not isinstance(caught.value, RunFailed)


def test_a_command_that_succeeds_costs_no_extra_docker_call():
    sandbox = FakeDocker()
    sandbox.open([]).run_command("true", 5)
    assert not [c for c in sandbox.calls if c[0] == "container"]


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


def test_stream_to_a_file_keeps_it_out_of_memory(tmp_path):
    sandbox = DockerSandbox(docker=sys.executable)
    with open(tmp_path / "out", "w+b") as file:
        result = sandbox._stream(["-I", "-c", "print('x' * 1000, end='')"], None, 30,
                                 stdout_file=file, stdout_limit=2000)
        assert result.stdout == b"" and not result.overflow
        file.seek(0)
        assert file.read() == b"x" * 1000


def test_stream_over_its_limit_is_killed_at_once(tmp_path):
    # As with a sparse file: endless output must not hold the run up until the deadline.
    sandbox = DockerSandbox(docker=sys.executable)
    endless = "import sys\nwhile True: sys.stdout.write('x' * 65536)"
    start = time.monotonic()
    with open(tmp_path / "out", "w+b") as file:
        result = sandbox._stream(["-I", "-c", endless], None, 30,
                                 stdout_file=file, stdout_limit=1000)
        assert result.overflow and file.tell() <= 1000
    assert time.monotonic() - start < 15


def test_stream_says_when_the_file_cannot_be_written(tmp_path):
    class Full(io.BytesIO):
        def write(self, data):
            raise OSError("No space left on device")

    sandbox = DockerSandbox(docker=sys.executable)
    with pytest.raises(JudgeError, match="No space left"):
        sandbox._stream(["-I", "-c", "print('x')"], None, 30, stdout_file=Full(),
                        stdout_limit=1000)


def test_stream_feeds_a_file_from_where_it_stands(tmp_path):
    sandbox = DockerSandbox(docker=sys.executable)
    with tempfile.TemporaryFile() as file:
        file.write(b"skip hello")
        file.seek(5)
        echoed = sandbox._stream(
            ["-I", "-c", "import sys; sys.stdout.write(sys.stdin.read())"], file, 30)
    assert echoed.stdout == b"hello"


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
    assert DockerSandbox(disk="1g", memory="2g").export_limit == 2 * 1024 ** 3


@pytest.mark.parametrize("streamed, message", [
    (Streamed(None, b"", b""), "timed out"),
    (Streamed(0, b"x", b"", overflow=True), "bytes as an archive"),
    (Streamed(0, b"not a tar archive at all" * 50, b""), "could not be copied out"),
    (Streamed(1, b"", b"container is not running"), "could not be copied out"),
])
def test_a_run_that_cannot_be_copied_out_fails(tmp_path, streamed, message):
    # The run can stop the container or tar, or leave a sparse file: left out of the
    # results instead, a skill could leave itself unscored.
    workspace = FakeDocker(streamed=lambda args, input: streamed).open([])
    with pytest.raises(RunFailed, match=message):
        workspace.export(tmp_path / "copy")


@pytest.mark.parametrize("streamed", [
    Streamed(None, b"", b""),
    Streamed(1, b"", b"Cannot connect to the Docker daemon"),
])
def test_a_copy_failing_because_docker_did_is_an_error(tmp_path, streamed):
    workspace = FakeDocker(streamed=lambda args, input: streamed,
                           inspect_container=completed(1, stderr="daemon is gone")).open([])
    with pytest.raises(JudgeError, match="Docker stopped answering") as caught:
        workspace.export(tmp_path / "copy")
    assert not isinstance(caught.value, RunFailed)


def test_export_keeps_links_that_only_go_down_and_drops_those_that_go_up(tmp_path):
    bs = chr(92)
    data = archive(**{"report.txt": b"ok", "sub/a.txt": b"a", "here": ("link", "report.txt"),
                      "down": ("link", "sub/a.txt"), "sub/up": ("link", "../report.txt"),
                      "sub/back": ("link", f"..{bs}report.txt")})
    workspace = FakeDocker(streamed=lambda args, input: Streamed(0, data, b"")).open([])
    workspace.export(tmp_path / "copy")
    copy = tmp_path / "copy"
    assert (copy / "here").read_bytes() == b"ok" and (copy / "down").read_bytes() == b"a"
    assert not (copy / "sub" / "up").exists() and not (copy / "sub" / "back").exists()


@pytest.mark.parametrize("name", ["a:b", "Z:foo"])
def test_a_name_this_system_reads_as_another_drive_does_not_stop_the_copy(tmp_path, name):
    data = archive(**{"report.txt": b"ok", name: b"x"})
    workspace = FakeDocker(streamed=lambda args, input: Streamed(0, data, b"")).open([])
    workspace.export(tmp_path / "copy")
    assert (tmp_path / "copy" / "report.txt").read_bytes() == b"ok"


def test_filter_failing_skips_the_member_rather_than_extract_it_unfiltered(monkeypatch):
    def broken(member, dest):
        raise OSError("name too long")

    monkeypatch.setattr(sandbox_module.tarfile, "data_filter", broken)
    assert sandbox_module._keep(tarfile.TarInfo("x"), "dest") is None


def test_export_reads_only_a_plain_tar(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("x")
        tar.addfile(info, io.BytesIO())
    data = buffer.getvalue()
    workspace = FakeDocker(streamed=lambda args, input: Streamed(0, data, b"")).open([])
    with pytest.raises(RunFailed):
        workspace.export(tmp_path / "copy")


def test_fixture_links_stay_links_so_nothing_outside_goes_in(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "a.txt").write_text("a", encoding="utf-8")
    try:
        (tmp_path / "files" / "link").symlink_to("a.txt")
    except OSError:
        pytest.skip("this system cannot make symlinks")
    buffer = io.BytesIO()
    sandbox_module._archive([tmp_path / "files"], buffer)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as tar:
        link = tar.getmember("files/link")
    assert link.issym() and link.linkname == "a.txt"


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


@pytest.mark.parametrize("version, safe", [
    ((3, 12, 9), False), ((3, 12, 10), False), ((3, 12, 11), True), ((3, 12, 16), True),
    ((3, 13, 0), False), ((3, 13, 3), False), ((3, 13, 4), True), ((3, 14, 0), True),
    ((3, 11, 9), False),
])
def test_only_pythons_with_tarfile_fixed_are_safe(version, safe):
    assert sandbox_module._safe_tarfile(version) is safe


def test_an_old_python_stops_runs_before_docker_is_asked(monkeypatch):
    monkeypatch.setattr(sandbox_module, "_safe_tarfile", lambda version: False)
    sandbox = FakeDocker()
    problem = sandbox.check()
    assert problem is not None and problem[0] is Problem.OLD_PYTHON
    assert "CVE-2025-4517" in problem[1] and "3.12.11" in problem[1]
    assert sandbox.unavailable() == problem[1] and sandbox.calls == []


# --- Containers left behind -----------------------------------------------------


def listing(*rows):
    return completed(stdout="".join(f"{row}\n" for row in rows))


def test_every_container_is_labelled_with_when_it_started(monkeypatch):
    monkeypatch.setattr(sandbox_module.time, "time", lambda: 1000.5)
    sandbox = FakeDocker()
    sandbox.open([])
    [run] = [c for c in sandbox.calls if c[0] == "run"]
    assert run[run.index("--label") + 1] == f"{STARTED_LABEL}=1000"


def test_sweep_removes_only_labelled_containers_past_their_age(monkeypatch):
    now = 10 * sandbox_module.ORPHAN_AGE
    monkeypatch.setattr(sandbox_module.time, "time", lambda: now)
    sandbox = FakeDocker()
    sandbox.listed = listing(f"old {now - sandbox_module.ORPHAN_AGE - 1}",
                             f"young {now - 60}", "nolabel", "odd notanumber", "x y z")
    sandbox.sweep()
    [ps, rm] = sandbox.calls
    assert f"label={STARTED_LABEL}" in ps
    assert rm == ("rm", "--force", "old")


def test_sweep_with_nothing_left_behind_removes_nothing():
    sandbox = FakeDocker()
    sandbox.sweep()
    assert removed(sandbox) == []


@pytest.mark.parametrize("error", [OSError("gone"), subprocess.TimeoutExpired("docker", 30)])
def test_a_sweep_failing_does_not_stop_runs(error):
    sandbox = FakeDocker()

    def broken(*args):
        if args[0] == "ps":
            raise error
        return completed(stdout="sha256:x")

    sandbox._docker = broken
    assert sandbox.unavailable() == ""


def test_sweep_runs_with_the_availability_check_but_not_with_check():
    sandbox = FakeDocker()
    sandbox.check()
    assert not [c for c in sandbox.calls if c[0] == "ps"]
    sandbox.unavailable()
    assert [c for c in sandbox.calls if c[0] == "ps"]
    broken = FakeDocker(inspect_image=FileNotFoundError("docker"))
    broken.unavailable()
    assert not [c for c in broken.calls if c[0] == "ps"]


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


def test_real_timeouts_are_told_apart_under_exec_latency(docker):
    workspace = docker.open([])
    try:
        # A process left holding the output makes `docker exec` wait about 2 s more after
        # the command ended, so a clock here reads well past the 1 s timeout.
        start = time.monotonic()
        assert workspace.run_command("setsid sleep 20 & exit 0", 1).exit_code == 0
        assert time.monotonic() - start > 1
        assert workspace.run_command("setsid sleep 20 & exit 3", 1).exit_code == 3

        # A command killed by itself, with a timeout it did not reach, against one it did.
        assert workspace.run_command("kill -9 $$", 10).exit_code == 137
        assert workspace.run_command("sleep 5", 0.5).exit_code is None
    finally:
        workspace.close()


def test_real_run_that_stops_its_container_fails(docker, tmp_path):
    workspace = docker.open([])
    try:
        with pytest.raises(RunFailed, match="stopped its sandbox"):
            workspace.run_command("kill -TERM 1; sleep 5", 30)
        with pytest.raises(RunFailed, match="stopped its sandbox"):
            workspace.run_command("exit 0", 30)  # not `docker exec`'s own exit code 1
        with pytest.raises(RunFailed, match="could not be copied out"):
            workspace.export(tmp_path / "copy")
    finally:
        workspace.close()


def test_real_sparse_file_fails_the_copy_at_once(docker, tmp_path):
    workspace = docker.open([])
    try:
        assert workspace.run_command("truncate -s 50G sparse", 30).exit_code == 0
        start = time.monotonic()
        with pytest.raises(RunFailed, match="bytes as an archive"):
            workspace.export(tmp_path / "copy")
        assert time.monotonic() - start < 15
    finally:
        workspace.close()


def test_real_links_survive_the_copy_in_and_out(docker, tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "a.txt").write_text("a", encoding="utf-8")
    try:
        (tmp_path / "files" / "link").symlink_to("a.txt")
    except OSError:
        pytest.skip("this system cannot make symlinks")
    workspace = docker.open([tmp_path / "files"])
    try:
        assert workspace.run_command("test -L files/link && cat files/link", 30).stdout == "a"
    finally:
        workspace.close()


def test_real_sweep_removes_only_what_is_old(docker, monkeypatch):
    now = time.time()
    monkeypatch.setattr(sandbox_module.time, "time", lambda: now - 2 * sandbox_module.ORPHAN_AGE)
    old = docker.open([])
    monkeypatch.undo()
    young = docker.open([])
    try:
        docker.sweep()
        assert young._running()
        with pytest.raises(JudgeError, match="Docker stopped answering"):
            old._running()  # no such container
    finally:
        old.close()
        young.close()


def test_real_files_go_in_through_the_spool(docker, tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 5_000_000)
    workspace = docker.open([tmp_path / "big.bin"])
    try:
        assert workspace.run_command("wc -c < big.bin", 30).stdout.strip() == "5000000"
    finally:
        workspace.close()


def test_real_skill_too_large_across_its_folders_fails(docker, tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "big.bin").write_bytes(b"x" * 40_000_000)
    workspace = docker.open([])
    try:
        workspace.put([tmp_path / "a" / "big.bin"], f"{SKILL_DIR}/a")  # under 64 MB alone
        with pytest.raises(RunFailed, match="skill is too large"):
            workspace.put([tmp_path / "b" / "big.bin"], f"{SKILL_DIR}/b")
    finally:
        workspace.close()
