import pytest

from metaskill.evaluate import dependencies
from metaskill.evaluate.dependencies import (
    SANDBOX, MissingDependencyError, missing_dependencies, require_dependencies)
from metaskill.evaluate.safety.cisco import INSTALL_COMMAND, CiscoBackend
from metaskill.evaluate.sandbox import DEFAULT_IMAGE, DOCKER_INSTALL_URL, DockerSandbox, Problem


class FakeSandbox(DockerSandbox):
    """DockerSandbox whose check() gives a fixed answer, without calling Docker."""

    def __init__(self, problem=None):
        super().__init__()
        self.problem = problem

    def check(self):
        return self.problem


@pytest.fixture
def cisco_installed(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: True)


def names(deps):
    return [(d.backend, d.package, d.required) for d in deps]


def test_nothing_missing(cisco_installed):
    assert missing_dependencies(FakeSandbox()) == []
    require_dependencies(FakeSandbox())  # does not raise


def test_docker_not_installed_lists_docker_and_the_image(cisco_installed):
    docker, image = missing_dependencies(
        FakeSandbox((Problem.NOT_INSTALLED, "Docker is not installed")))
    # Docker is installed by the user, from the URL; the image by a command.
    assert (docker.package, docker.install_command, docker.install_url) == (
        "Docker", "", DOCKER_INSTALL_URL)
    assert docker.reason == "Docker is not installed"
    assert (image.package, image.install_command) == (DEFAULT_IMAGE, f"docker pull {DEFAULT_IMAGE}")
    assert docker.required and image.required


def test_docker_not_running_lists_only_docker(cisco_installed):
    [docker] = missing_dependencies(
        FakeSandbox((Problem.NOT_RUNNING, "Docker is not usable: daemon not running")))
    assert docker.package == "Docker" and docker.install_url == ""
    assert docker.reason == "Docker is not usable: daemon not running"


def test_image_not_pulled_lists_the_pull_command(cisco_installed):
    [image] = missing_dependencies(FakeSandbox((Problem.NO_IMAGE, "not pulled")))
    assert image.install_command == f"docker pull {DEFAULT_IMAGE}" and image.required


def test_cisco_is_listed_after_docker_and_is_optional(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: False)
    deps = missing_dependencies(FakeSandbox((Problem.NO_IMAGE, "not pulled")))
    assert names(deps) == [(SANDBOX, DEFAULT_IMAGE, True), ("cisco", "cisco-ai-skill-scanner", False)]
    assert deps[1].install_command == INSTALL_COMMAND


def test_require_raises_only_for_required(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: False)
    require_dependencies(FakeSandbox())  # Cisco missing alone is not enough to stop

    with pytest.raises(MissingDependencyError, match="Docker \\(Docker is not installed\\)") as e:
        require_dependencies(FakeSandbox((Problem.NOT_INSTALLED, "Docker is not installed")))
    assert [d.package for d in e.value.missing] == ["Docker", DEFAULT_IMAGE]


def test_default_sandbox_is_docker(monkeypatch, cisco_installed):
    built = []
    monkeypatch.setattr(dependencies, "DockerSandbox", lambda: built.append(1) or FakeSandbox())
    assert missing_dependencies() == [] and built == [1]
