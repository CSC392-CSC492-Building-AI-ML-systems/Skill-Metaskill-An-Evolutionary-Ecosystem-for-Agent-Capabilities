"""Test task loading (docs/evaluate.md 4.3).

Tasks are human-written and stored in a JSON or YAML file: either a list of tasks or an
object with a `tasks` list. Each task has the shape of `Task.to_dict()`. Fixture paths
are relative to the folder the file is in, and must stay in it: fixtures are copied
into the agent's folder, so one outside it could hand the agent any of the user's files.
docs/writing-tasks.md is the guide for whoever writes one.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .judge import outside_run_folder
from .models import CheckKind, JudgeType, Task, load_yaml

# A name that becomes part of a path: task_id in a log file name
# ([task_id]-[variant]-r[run].txt), and the skill's name in its log folder (runner.py).
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class TaskError(ValueError):
    """A task file that cannot be used as written. `problems` holds one line for each
    problem found, without the file's path."""

    def __init__(self, path: Path, problems: list[str]):
        self.problems = problems
        super().__init__(f"{path}: {'; '.join(problems)}")


@dataclass
class TaskFile:
    tasks: list[Task]
    warnings: list[str]  # what is ignored, so probably a mistake


# The fields of a task and of a check (docs/writing-tasks.md). Others are ignored.
_TASK_FIELDS = ("task_id", "description", "prompt", "checks", "fixtures")
_CHECK_FIELDS = ("judge", "kind", "target", "expected", "checklist")


def load_tasks(path: str | Path) -> list[Task]:
    """The tasks in a JSON or YAML file, with fixture paths made absolute."""
    return read_tasks(path).tasks


def read_tasks(path: str | Path) -> TaskFile:
    """load_tasks, with the warnings. Raises TaskError listing every problem in the file,
    including checks that could only fail once an agent had run."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as e:
        raise TaskError(path, [f"cannot read the file: {e}"]) from e
    data = _parse(path, text)
    problems: list[str] = []
    warnings: list[str] = []
    items = data
    if isinstance(data, dict):
        items = data.get("tasks")
        warnings += _unknown(data, ("tasks",), "the file")
    if not isinstance(items, list) or not items:
        raise TaskError(path, ["expected a non-empty list of tasks, or an object with a "
                               "`tasks` list"])

    tasks: list[Task] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"task {i + 1}"
        if not isinstance(item, dict):
            problems.append(f"{where} is not an object")
            continue
        task_id = item.get("task_id")
        if isinstance(task_id, str) and SAFE_NAME.fullmatch(task_id):
            where = f"{where} ({task_id})"
        found = _task_problems(item, warnings, where)
        if isinstance(task_id, str):
            if task_id in seen:
                found.append(f"duplicate task_id {task_id!r}: give each task its own")
            seen.add(task_id)
        fixtures, bad = _fixtures(path.parent, item.get("fixtures", []))
        found += bad
        if found:
            problems += [f"{where}: {problem}" for problem in found]
            continue
        task = Task.from_dict({**item, "fixtures": [],
                               "checks": [_as_check(c) for c in item["checks"]]})
        task.fixtures = fixtures
        tasks.append(task)
    if problems:
        raise TaskError(path, problems)
    return TaskFile(tasks, warnings)


def _task_problems(item: dict, warnings: list[str], where: str) -> list[str]:
    """What is wrong with one task, apart from its fixtures and a repeated id."""
    warnings += _unknown(item, _TASK_FIELDS, where)
    problems = []
    for name in ("task_id", "description", "prompt"):
        if name not in item:
            problems.append(f"missing field `{name}`")
        elif not isinstance(item[name], str):
            problems.append(f"`{name}` must be text: put it in quotes")
        elif name == "prompt" and not item[name].strip():
            problems.append("`prompt` is empty: say what the agent has to do")
    task_id = item.get("task_id")
    if isinstance(task_id, str) and not SAFE_NAME.fullmatch(task_id):
        problems.append(f"task_id {task_id!r} may only contain letters, digits, '_', '.' "
                        "and '-', and must start with a letter or digit")
    checks = item.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("has no checks: `checks` must be a non-empty list")
        return problems
    for n, check in enumerate(checks, 1):
        label = f"check {n}"
        if not isinstance(check, dict):
            problems.append(f"{label} is not an object")
            continue
        warnings += _unknown(check, _CHECK_FIELDS, f"{where}, {label}")
        problem = _check_problem(check)
        if problem:
            problems.append(f"{label}: {problem}")
        else:
            warnings += [f"{where}, {label}: `{name}` is ignored by this check"
                         for name in _unused(check) if check.get(name)]
    return problems


def _check_problem(check: dict) -> str:
    """Why a check could not be judged, or "". The same rules as judge.py, applied before
    any agent runs."""
    judges = ", ".join(j.value for j in JudgeType)
    kinds = ", ".join(k.value for k in CheckKind)
    judge = check.get("judge")
    if judge not in list(JudgeType):
        return (f"`judge` must be one of {judges}" if "judge" in check else
                f"missing field `judge`: one of {judges}")
    kind = check.get("kind")
    if kind and kind not in list(CheckKind):
        return f"unknown kind {_shown(kind)}: use one of {kinds}"
    if judge == JudgeType.LLM:
        items = check.get("checklist")
        if (not isinstance(items, list) or not items
                or not all(isinstance(i, str) and i.strip() for i in items)):
            return "an llm check needs `checklist`: a non-empty list of texts"
        return ""
    if not kind:
        return f"a deterministic check needs `kind`: one of {kinds}"
    target = check.get("target", "")
    expected = check.get("expected")
    expected = "" if expected is None else expected
    if kind == CheckKind.OUTPUT_MATCHES:
        if not isinstance(expected, str) or not expected:
            return "output_matches needs `expected`: a regular expression, as text"
        try:
            re.compile(expected, re.MULTILINE)
        except (re.error, RecursionError, OverflowError) as e:
            return f"`expected` is not a valid regular expression: {e}"
        return ""
    if not isinstance(target, str) or not target.strip():
        what = "a command" if kind == CheckKind.EXIT_CODE else "a file path"
        return f"{kind} needs `target`: {what}"
    if kind == CheckKind.FILE_EXISTS:
        if outside_run_folder(target):
            return (f"target {_shown(target)} is outside the run folder: write it relative "
                    "to that folder, e.g. report.txt")
        return ""
    if expected != "":
        try:
            if isinstance(expected, (bool, float)):
                raise ValueError
            int(expected)
        except (TypeError, ValueError):
            return f"exit_code `expected` must be a whole number, not {_shown(expected)}"
    return ""


def _as_check(check: dict) -> dict:
    """A valid check in the types Check holds: a field may be left empty, and YAML and
    JSON may give the exit code as a number."""
    values = {name: check.get(name) or "" for name in ("target", "expected")}
    expected = check.get("expected")
    if isinstance(expected, int):
        values["expected"] = str(expected)
    return {**check, **values, "checklist": check.get("checklist") or []}


def _unused(check: dict) -> tuple[str, ...]:
    if check["judge"] == JudgeType.LLM:
        return ("kind", "target", "expected")
    return {CheckKind.FILE_EXISTS: ("expected", "checklist"),
            CheckKind.OUTPUT_MATCHES: ("target", "checklist"),
            CheckKind.EXIT_CODE: ("checklist",)}[check["kind"]]


def _unknown(item: dict, fields: tuple[str, ...], where: str) -> list[str]:
    warnings = []
    for name in item:
        if name not in fields:
            close = difflib.get_close_matches(str(name), fields, n=1)
            hint = f"; did you mean `{close[0]}`?" if close else ""
            warnings.append(f"{where}: unknown field {_shown(name)} is ignored{hint}")
    return warnings


def _shown(value: Any) -> str:
    """A value from the file, short enough for a message."""
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _fixtures(root: Path, fixtures: Any) -> tuple[list[Path], list[str]]:
    """A task's fixtures as absolute paths, and what is wrong with them."""
    if not isinstance(fixtures, list) or not all(isinstance(f, str) and f for f in fixtures):
        return [], ["`fixtures` must be a list of file or folder paths"]
    found: list[Path] = []
    problems: list[str] = []
    for fixture in fixtures:
        try:
            found.append(_fixture(root, Path(fixture)))
        except _BadFixture as e:
            problems.append(str(e))
        except (OSError, RuntimeError, ValueError) as e:  # e.g. a name too long
            problems.append(f"fixture {_shown(fixture)} cannot be read "
                            f"({type(e).__name__})")
    names = [f.name for f in found]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:  # fixtures are copied by name, so one would replace the other
        problems.append(f"two fixtures are named {twice[0]!r}: they are copied by name, "
                        "so rename one")
    return found, problems


def _parse(path: Path, text: str) -> Any:
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                return load_yaml(text)
            except ImportError as e:
                raise TaskError(path, [f"{e}, or use JSON"]) from e
        return json.loads(text)
    except TaskError:
        raise
    except Exception as e:  # json.JSONDecodeError, or the YAML loader's error
        raise TaskError(path, [f"not valid {path.suffix.lstrip('.') or 'JSON'}: {e}"]) from e


class _BadFixture(Exception):
    pass


def _fixture(root: Path, fixture: Path) -> Path:
    root = root.resolve()
    resolved = (root / fixture).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise _BadFixture(f"fixture {fixture} is outside the task file's folder")
    if not resolved.exists():
        raise _BadFixture(f"fixture {fixture} does not exist: paths start at the "
                          "task file's folder")
    # A link in a fixture folder is copied as a link (sandbox._archive), so it must not
    # lead out of the folder, and must still work in the container, where only the
    # fixture is. A hard link cannot be told apart from a file outside, so it is refused.
    for path in [resolved, *(resolved.rglob("*") if resolved.is_dir() else [])]:
        target = path.resolve()
        if not target.is_relative_to(root):
            raise _BadFixture(f"fixture {fixture} links to a file outside the task "
                              "file's folder")
        if path.is_symlink():
            written = os.readlink(path)
            if (not target.is_relative_to(resolved) or not target.exists()
                    or os.path.isabs(written) or "\\" in written):
                raise _BadFixture(f"{path.name} in fixture {fixture} would be a broken "
                                  "link in the sandbox: it must be relative, with `/`, "
                                  "and lead to a file in the same fixture")
        if path.is_file() and path.stat().st_nlink > 1:
            raise _BadFixture(f"fixture {fixture} has a hard link, so it may be a file "
                              "outside the task file's folder")
    return resolved
