# `dependencies.py`: What evaluate needs installed

Code: [`metaskill/evaluate/dependencies.py`](../../metaskill/evaluate/dependencies.py). Tests: [`tests/test_dependencies.py`](../../tests/test_dependencies.py).

`evaluate` needs software that a plugin cannot install: Docker for the sandbox ([runner.md](runner.md) 2.6), and Cisco's scanner for safety ([safety/score.md](../safety/score.md)). This module lists what is missing so the agent can ask the user, the same way safety already does for Cisco. It never installs or prompts.

Only `evaluate` needs these. `generalize` rewrites skill files and runs no code from them, so it needs no Docker and never calls this.

## Interface

```python
def missing_dependencies(sandbox: DockerSandbox | None = None) -> list[Dependency]: ...
def require_dependencies(sandbox: DockerSandbox | None = None) -> None: ...  # raises MissingDependencyError
```

- **`missing_dependencies`**: everything missing, required ones first. The tool calls it once before evaluating, and the agent shows each entry to the user.
- **`require_dependencies`**: raises `MissingDependencyError` (with `.missing`) if a required one is still missing. Evaluate calls it after the user has had the chance to install, and does not start if it raises.

`sandbox` defaults to a `DockerSandbox()`; tests pass one whose `check()` is faked.

## What is listed

`Dependency` is the one from `safety` ([safety/score.md](../safety/score.md)):

| Field | Meaning |
|---|---|
| `backend` | `sandbox` for Docker and its image, `cisco` for the scanner |
| `package` | What is missing: `Docker`, the image name, or the pip package |
| `install_command` | A command the agent can run with the user's OK; `""` if the user installs it by hand |
| `required` | `True`: evaluate does not run without it. `False`: it runs with less |
| `install_url` | Where to get it, when no command installs it |
| `reason` | What is wrong, from the check |

| Situation (`DockerSandbox.check()`) | Listed | Required |
|---|---|---|
| Docker not installed (`not-installed`) | `Docker` (by hand, `install_url`), then the image (`docker pull python:3.12-slim@sha256:...`, pinned in `sandbox.DEFAULT_IMAGE`) | Yes |
| Docker installed but not answering or refusing access (`not-running`) | `Docker`, with Docker's own message as `reason`. The user starts Docker, or on Linux gets access to its socket | Yes |
| Image not pulled (`no-image`) | The image, with its `docker pull` command | Yes |
| Cisco's scanner not installed | `cisco-ai-skill-scanner`, with its `pip install` command | No: safety is scored by our own checks only, as `partial` |

When Docker is not installed, the image is listed too, so the user sees both steps at once instead of one per attempt.

The runner still checks the sandbox itself once before running anything (runner.md 2.6). That is a second line of defence if Docker stops between this check and the run: no run starts, and nothing runs on the host.

## Open

- The `evaluate` tool operation does not exist yet (`tools.py` routes only `generalize` and `personalize`). When it is added, it calls `missing_dependencies()` before evaluating and `require_dependencies()` before starting.
