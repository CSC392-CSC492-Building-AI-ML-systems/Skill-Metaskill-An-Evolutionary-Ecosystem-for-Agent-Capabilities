import json

import pytest

from metaskill.evaluate.models import CheckKind, JudgeType
from metaskill.evaluate.tasks import TaskError, load_tasks

TASK = {
    "task_id": "upload-single-file",
    "description": "Upload ./a.txt to bucket X",
    "prompt": "Upload a.txt to the bucket",
    "checks": [{"judge": "deterministic", "kind": "exit_code", "target": "test -f a.txt"}],
    "fixtures": ["files/a.txt"],
}


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "a.txt").write_text("a", encoding="utf-8")
    return tmp_path


def write(folder, data, name="tasks.json"):
    path = folder / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_loads_a_json_list_and_resolves_fixtures(folder):
    [task] = load_tasks(write(folder, [TASK]))
    assert task.task_id == "upload-single-file"
    assert task.checks[0].judge is JudgeType.DETERMINISTIC
    assert task.checks[0].kind is CheckKind.EXIT_CODE
    assert task.fixtures == [(folder / "files" / "a.txt").resolve()]


def test_loads_an_object_with_a_tasks_list(folder):
    assert len(load_tasks(write(folder, {"tasks": [TASK]}))) == 1


def test_loads_yaml(folder):
    yaml = pytest.importorskip("yaml")
    assert len(load_tasks(write(folder, yaml.safe_dump({"tasks": [TASK]}), "tasks.yaml"))) == 1


@pytest.mark.parametrize("data, message", [
    ([], "non-empty list"),
    ({"tasks": "nope"}, "non-empty list"),
    ("{not json", "not valid json"),
    (["text"], "is not an object"),
    ([{**TASK, "checks": []}], "has no checks"),
    ([{"task_id": "x"}], "KeyError"),
    ([TASK, TASK], "duplicate task_id"),
    ([{**TASK, "task_id": "../escape"}], "may only contain"),
    ([{**TASK, "fixtures": ["missing.txt"]}], "does not exist"),
])
def test_rejects_unusable_task_files(folder, data, message):
    with pytest.raises(TaskError, match=message):
        load_tasks(write(folder, data))


def test_missing_file(tmp_path):
    with pytest.raises(TaskError, match="cannot read"):
        load_tasks(tmp_path / "missing.json")
