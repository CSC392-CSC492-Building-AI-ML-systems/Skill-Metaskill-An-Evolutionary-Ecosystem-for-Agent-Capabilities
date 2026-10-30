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
    """The task file's folder, inside tmp_path so a file can be put outside it."""
    folder = tmp_path / "tasks"
    for sub in ("files", "other"):
        (folder / sub).mkdir(parents=True)
        (folder / sub / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    return folder


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
    ([{**TASK, "fixtures": ["../outside.txt"]}], "outside the task file's folder"),
    ([{**TASK, "fixtures": ["."]}], "outside the task file's folder"),
    ([{**TASK, "fixtures": ["files/a.txt", "other/a.txt"]}], "two fixtures are named 'a.txt'"),
])
def test_rejects_unusable_task_files(folder, data, message):
    with pytest.raises(TaskError, match=message):
        load_tasks(write(folder, data))


def test_absolute_fixture_outside_the_folder_is_refused(folder):
    outside = str((folder.parent / "outside.txt").resolve())
    with pytest.raises(TaskError, match="outside the task file's folder"):
        load_tasks(write(folder, [{**TASK, "fixtures": [outside]}]))


def test_link_in_a_fixture_folder_leading_out_is_refused(folder):
    try:
        (folder / "files" / "key").symlink_to(folder.parent / "outside.txt")
    except OSError:
        pytest.skip("this system cannot make symlinks")
    with pytest.raises(TaskError, match="links to a file outside"):
        load_tasks(write(folder, [{**TASK, "fixtures": ["files"]}]))


def test_hard_linked_fixture_is_refused(folder):
    try:
        (folder / "files" / "key").hardlink_to(folder.parent / "outside.txt")
    except OSError:
        pytest.skip("this system cannot make hard links")
    with pytest.raises(TaskError, match="hard link"):
        load_tasks(write(folder, [{**TASK, "fixtures": ["files/key"]}]))


def test_missing_file(tmp_path):
    with pytest.raises(TaskError, match="cannot read"):
        load_tasks(tmp_path / "missing.json")
