"""Test task loading (docs/evaluate.md 4.3).

Tasks are human-written and stored in a JSON or YAML file: either a list of tasks or an
object with a `tasks` list. Each task has the shape of `Task.to_dict()`. Fixture paths
are relative to the folder the file is in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Task

# task_id becomes part of a log file name ([task_id]-[variant]-r[run].txt).
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


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
        if not _TASK_ID.fullmatch(task.task_id):
            raise TaskError(f"{where}: task_id {task.task_id!r} may only contain "
                            "letters, digits, '_', '.' and '-'")
        if task.task_id in seen:
            raise TaskError(f"{where}: duplicate task_id {task.task_id!r}")
        seen.add(task.task_id)
        task.fixtures = [_fixture(path.parent, f, where) for f in task.fixtures]
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
    resolved = (root / fixture).resolve()
    if not resolved.exists():
        raise TaskError(f"{where}: fixture {fixture} does not exist")
    return resolved
