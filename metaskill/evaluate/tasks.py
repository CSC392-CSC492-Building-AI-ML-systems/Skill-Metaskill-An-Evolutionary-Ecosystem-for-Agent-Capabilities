"""Test task loading (docs/evaluate.md 4.3).

Tasks are human-written and stored in a JSON or YAML file: either a list of tasks or an
object with a `tasks` list. Each task has the shape of `Task.to_dict()`. Fixture paths
are relative to the folder the file is in, and must stay in it: fixtures are copied
into the agent's folder, so one outside it could hand the agent any of the user's files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Task

# A name that becomes part of a path: task_id in a log file name
# ([task_id]-[variant]-r[run].txt), and the skill's name in its log folder (runner.py).
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class TaskError(ValueError):
    """A task file that cannot be used as written."""


def load_tasks(path: str | Path) -> list[Task]:
    """The tasks in a JSON or YAML file, with fixture paths made absolute."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise TaskError(f"cannot read task file {path}: {e}") from e
    data = _parse(path, text)
    items = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise TaskError(f"{path}: expected a non-empty list of tasks")

    tasks: list[Task] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"{path}: task {i + 1}"
        if not isinstance(item, dict):
            raise TaskError(f"{where} is not an object")
        try:
            task = Task.from_dict(item)
        except (KeyError, TypeError, ValueError) as e:
            raise TaskError(f"{where}: {type(e).__name__}: {e}") from e
        if not SAFE_NAME.fullmatch(task.task_id):
            raise TaskError(f"{where}: task_id {task.task_id!r} may only contain "
                            "letters, digits, '_', '.' and '-'")
        if task.task_id in seen:
            raise TaskError(f"{where}: duplicate task_id {task.task_id!r}")
        seen.add(task.task_id)
        task.fixtures = [_fixture(path.parent, f, where) for f in task.fixtures]
        names = [f.name for f in task.fixtures]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:  # fixtures are copied by name, so one would replace the other
            raise TaskError(f"{where}: two fixtures are named {twice[0]!r}")
        tasks.append(task)
    return tasks


def _parse(path: Path, text: str) -> Any:
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as e:
                raise TaskError(
                    "PyYAML is required for YAML task files. "
                    "Install it with `pip install pyyaml`, or use JSON.") from e
            return yaml.safe_load(text)
        return json.loads(text)
    except TaskError:
        raise
    except Exception as e:  # json.JSONDecodeError, yaml.YAMLError
        raise TaskError(f"{path}: not valid {path.suffix.lstrip('.') or 'JSON'}: {e}") from e


def _fixture(root: Path, fixture: Path, where: str) -> Path:
    root = root.resolve()
    resolved = (root / fixture).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise TaskError(f"{where}: fixture {fixture} is outside the task file's folder")
    if not resolved.exists():
        raise TaskError(f"{where}: fixture {fixture} does not exist")
    # Copying follows links, so a link in a fixture folder must not lead out of it. A
    # hard link cannot be told apart from a file outside, so it is refused too.
    for path in [resolved, *(resolved.rglob("*") if resolved.is_dir() else [])]:
        if not path.resolve().is_relative_to(root):
            raise TaskError(f"{where}: fixture {fixture} links to a file outside the "
                            "task file's folder")
        if path.is_file() and path.stat().st_nlink > 1:
            raise TaskError(f"{where}: fixture {fixture} has a hard link, so it may be a "
                            "file outside the task file's folder")
    return resolved
