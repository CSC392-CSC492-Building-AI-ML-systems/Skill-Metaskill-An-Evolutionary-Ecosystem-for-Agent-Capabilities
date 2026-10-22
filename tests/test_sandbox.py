import json
import subprocess
from types import SimpleNamespace

import pytest

from metaskill.evaluate import sandbox as sandbox_module
from metaskill.evaluate.judge import JudgeError
from metaskill.evaluate.sandbox import SANDBOX_PATH, WORKDIR, DockerSandbox


class FakeProcess:
    def __init__(self, hangs=False):
        self.hangs, self.killed, self.done = hangs, False, not hangs

    def wait(self, timeout=None):
        if self.hangs and not self.killed:
            raise subprocess.TimeoutExpired("docker", timeout)
        self.done = True

    def poll(self):
        return 0 if self.done else None

    def kill(self):
        self.killed = True


class FakeDocker(DockerSandbox):
    """DockerSandbox with Docker replaced: `state` is what `container inspect` reports
    (None: the container does not exist), `stderr` what the command writes."""

    def __init__(self, state=None, stderr=b"", hangs=False, inspect_image=None):
        super().__init__()
        self.state = {"ExitCode": 0, "Error": ""} if state is None else state
        self.stderr, self.hangs, self.inspect_image = stderr, hangs, inspect_image
        self.calls, self.started, self.process = [], [], None

    def _docker(self, *args):
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            if isinstance(self.inspect_image, Exception):
                raise self.inspect_image
            return self.inspect_image or SimpleNamespace(returncode=0, stdout="sha256:x", stderr="")
        if args[:2] == ("container", "inspect"):
            if self.state is False:
                return SimpleNamespace(returncode=1, stdout="", stderr="No such container")
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.state), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def _start(self, args, stderr):
        self.started.append(args)
        stderr.write(self.stderr)
        self.process = FakeProcess(self.hangs)
        return self.process


def removed(sandbox):
    return [c for c in sandbox.calls if c[:2] == ("rm", "--force")]


# --- The container ------------------------------------------------------------


def test_container_is_isolated(tmp_path):
    sandbox = FakeDocker()
    sandbox.run_command("python test.py", tmp_path, 5)
    [args] = sandbox.started
    joined = " ".join(args)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--pull never", "--init",
                 "--security-opt no-new-privileges", f"--workdir {WORKDIR}"):
        assert flag in joined
    assert f"type=bind,source={tmp_path.resolve()},target={WORKDIR}" in args
    # The run's folder is the only mount, and only PATH and HOME are set.
    assert sum(a == "--mount" for a in args) == 1 and "-v" not in args
    assert [args[i + 1] for i, a in enumerate(args) if a == "--env"] == [
        f"PATH={SANDBOX_PATH}", "HOME=/tmp"]
    assert WORKDIR not in SANDBOX_PATH.split(":")
    # The command is one argument to sh, after the image.
    assert args[-3:] == [sandbox.image, "-c", "python test.py"]


def test_exit_code_and_stderr_come_back(tmp_path):
    sandbox = FakeDocker(state={"ExitCode": 3, "Error": ""}, stderr=b"x" * 5000 + b"the end")
    code, stderr = sandbox.run_command("python test.py", tmp_path, 5)
    assert code == 3
    assert stderr.endswith("the end") and len(stderr) == sandbox_module.STDERR_TAIL
    assert len(removed(sandbox)) == 1


def test_a_command_imitating_dockers_own_failure_is_just_an_exit_code(tmp_path):
    # `docker run` exits 125 when Docker fails; the code is read from the container instead.
    code, _ = FakeDocker(state={"ExitCode": 125, "Error": ""}).run_command("exit 125", tmp_path, 5)
    assert code == 125


def test_docker_failures_are_judge_errors(tmp_path):
    with pytest.raises(JudgeError, match="sandbox failed: exec failed"):
        FakeDocker(state={"ExitCode": 127, "Error": "exec failed"}).run_command("x", tmp_path, 5)
    never_started = FakeDocker(state=False, stderr=b"docker: invalid reference format")
    with pytest.raises(JudgeError, match="did not start: docker: invalid reference"):
        never_started.run_command("x", tmp_path, 5)
    assert len(removed(never_started)) == 1


def test_timeout_kills_the_container(tmp_path):
    sandbox = FakeDocker(hangs=True, stderr=b"still going")
    assert sandbox.run_command("sleep 999", tmp_path, 0.1) == (None, "still going")
    assert len(removed(sandbox)) == 1 and sandbox.process.killed


def test_docker_missing_at_run_time_is_a_judge_error(tmp_path):
    class Missing(FakeDocker):
        def _start(self, args, stderr):
            raise FileNotFoundError("docker")

    with pytest.raises(JudgeError, match="could not start Docker"):
        Missing().run_command("x", tmp_path, 5)


def test_folder_with_a_comma_is_refused(tmp_path):
    folder = tmp_path / "a,b"
    folder.mkdir()
    with pytest.raises(JudgeError, match="comma"):
        FakeDocker().run_command("x", folder, 5)


# --- Availability ---------------------------------------------------------------


def test_available_when_the_image_is_there():
    assert FakeDocker().unavailable() == ""


@pytest.mark.parametrize("inspect, reason", [
    (FileNotFoundError("docker"), "Docker is not installed"),
    (subprocess.TimeoutExpired("docker", 30), "Docker did not answer"),
    (SimpleNamespace(returncode=1, stdout="", stderr="Error: No such image: python:3.12-slim"),
     "run `docker pull python:3.12-slim`"),
    (SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"),
     "Docker is not usable: Cannot connect to the Docker daemon"),
])
def test_unavailable_says_why(inspect, reason):
    assert reason in FakeDocker(inspect_image=inspect).unavailable()


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
    assert docker.run_command("python test.py", tmp_path, 60)[0] == 3

    # Writes land in the run folder; the rest of the system is read-only.
    assert docker.run_command("echo hi > out.txt", tmp_path, 60)[0] == 0
    assert (tmp_path / "out.txt").read_text() == "hi\n"
    assert docker.run_command("touch /etc/x", tmp_path, 60)[0] != 0

    # No network, and none of the host's environment.
    monkeypatch.setenv("METASKILL_SECRET", "leaked")
    assert docker.run_command('test -z "$METASKILL_SECRET"', tmp_path, 60)[0] == 0
    no_net = "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""
    assert docker.run_command(no_net, tmp_path, 60)[0] != 0

    assert docker.run_command("sleep 30", tmp_path, 2)[0] is None
