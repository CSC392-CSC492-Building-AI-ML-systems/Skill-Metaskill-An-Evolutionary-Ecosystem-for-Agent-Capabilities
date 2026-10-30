"""What `evaluate` needs that is not installed, for the install prompt
(docs/evaluate/dependencies.md).

The tool calls missing_dependencies() once, before evaluating, and the agent asks the
user about each entry, as for Cisco's scanner (docs/safety/score.md). Nothing here
installs or prompts. Only `evaluate` needs these: generalize runs no code from skills, so
it never calls this.
"""

from __future__ import annotations

from . import safety
from .safety import Dependency
from .sandbox import DOCKER_INSTALL_URL, DockerSandbox, Problem

SANDBOX = "sandbox"  # Dependency.backend of Docker and its image


class MissingDependencyError(RuntimeError):
    """A required dependency is missing, so evaluate cannot start."""

    def __init__(self, missing: list[Dependency]):
        self.missing = missing
        super().__init__("evaluate needs: " + "; ".join(
            f"{d.package} ({d.reason})" if d.reason else d.package for d in missing))


def missing_dependencies(sandbox: DockerSandbox | None = None) -> list[Dependency]:
    """Everything `evaluate` needs that is missing, required ones first.

    Docker is required: agent runs and exit_code checks run in it (runner.md 2.6). Cisco's scanner is
    not: without it, safety is scored by our own checks only, as `partial`.
    """
    return _sandbox(sandbox or DockerSandbox()) + safety.missing_dependencies()


def require_dependencies(sandbox: DockerSandbox | None = None) -> None:
    """Raise MissingDependencyError if a required dependency is missing. For the start
    of evaluate, after the user had the chance to install what missing_dependencies()
    listed."""
    missing = [d for d in missing_dependencies(sandbox) if d.required]
    if missing:
        raise MissingDependencyError(missing)


def _sandbox(sandbox: DockerSandbox) -> list[Dependency]:
    problem = sandbox.check()
    if problem is None:
        return []
    kind, reason = problem
    image = Dependency(SANDBOX, sandbox.image, sandbox.pull_command, required=True,
                       reason="" if kind is Problem.NOT_INSTALLED else reason)
    if kind is Problem.NO_IMAGE:
        return [image]
    # Docker itself cannot be installed by a command the agent runs: the user installs
    # or starts it.
    docker = Dependency(SANDBOX, "Docker", "", required=True, reason=reason,
                        install_url=DOCKER_INSTALL_URL if kind is Problem.NOT_INSTALLED else "")
    # Without Docker the image cannot be there either, so both are listed at once rather
    # than one per attempt. A Docker that is not running may already have it.
    return [docker, image] if kind is Problem.NOT_INSTALLED else [docker]
