import json
import sys
import types

import pytest

from metaskill.evaluate.models import CheckKind, JudgeType
from metaskill.evaluate.tasks import TaskError, load_tasks, read_tasks

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


def test_yaml_goes_through_hermes_loader(folder, monkeypatch):
    loader = types.SimpleNamespace(safe_load=lambda text: {"tasks": [TASK]})
    monkeypatch.setitem(sys.modules, "hermes_yaml", loader)
    assert len(load_tasks(write(folder, "tasks: []", "tasks.yaml"))) == 1


def test_yaml_outside_hermes_says_to_use_json(folder, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_yaml", None)
    with pytest.raises(TaskError, match="use JSON"):
        load_tasks(write(folder, "tasks: []", "tasks.yaml"))


@pytest.mark.parametrize("data, message", [
    ([], "non-empty list"),
    ({"tasks": "nope"}, "non-empty list"),
    ("{not json", "not valid json"),
    (["text"], "is not an object"),
    ([{**TASK, "checks": []}], "has no checks"),
    ([{"task_id": "x"}], "missing field `description`"),
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


def link(path, target):
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this system cannot make symlinks")


def test_link_inside_a_fixture_folder_is_allowed(folder):
    link(folder / "files" / "link", "a.txt")
    [task] = load_tasks(write(folder, [{**TASK, "fixtures": ["files"]}]))
    assert task.fixtures == [(folder / "files").resolve()]


def test_link_to_a_file_in_another_fixture_folder_is_refused(folder):
    # In the task file's folder, but only the fixture reaches the sandbox: it would dangle.
    link(folder / "files" / "link", "../other/a.txt")
    with pytest.raises(TaskError, match="broken link in the sandbox"):
        load_tasks(write(folder, [{**TASK, "fixtures": ["files"]}]))


def test_absolute_link_in_a_fixture_folder_is_refused(folder):
    # Inside the fixture here, but that path does not exist in the sandbox.
    link(folder / "files" / "link", (folder / "files" / "a.txt").resolve())
    with pytest.raises(TaskError, match="broken link in the sandbox"):
        load_tasks(write(folder, [{**TASK, "fixtures": ["files"]}]))


def test_broken_link_in_a_fixture_folder_is_refused(folder):
    link(folder / "files" / "link", "missing.txt")
    with pytest.raises(TaskError, match="broken link in the sandbox"):
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


def check(**fields):
    return [{**TASK, "checks": [fields]}]


def deterministic(kind, **fields):
    return check(judge="deterministic", kind=kind, **fields)


@pytest.mark.parametrize("data, message", [
    # Used to crash, rather than say what is wrong.
    ([{**TASK, "checks": "file_exists"}], "`checks` must be a non-empty list"),
    ([{**TASK, "checks": ["file_exists"]}], "check 1 is not an object"),
    ([{**TASK, "task_id": 7}], "`task_id` must be text"),
    ([{**TASK, "prompt": 5}], "`prompt` must be text"),
    ([{**TASK, "prompt": "  "}], "`prompt` is empty"),
    ([{**TASK, "description": None}], "`description` must be text"),
    ([{**TASK, "fixtures": "files/a.txt"}], "`fixtures` must be a list"),
    ([{**TASK, "fixtures": [3]}], "`fixtures` must be a list"),
    ([{**TASK, "fixtures": ["a" * 300]}], "cannot be read|does not exist"),
    (check(kind="file_exists", target="a"), "missing field `judge`"),
    (check(judge="lm"), "`judge` must be one of deterministic, llm"),
    (check(judge="deterministic"), "needs `kind`: one of file_exists, exit_code"),
    (deterministic("file_exist", target="a"), "unknown kind 'file_exist': use one of"),
    (deterministic("file_exists"), "needs `target`: a file path"),
    (deterministic("file_exists", target="/etc/passwd"), "outside the run folder"),
    (deterministic("file_exists", target="a/../../b"), "outside the run folder"),
    (deterministic("exit_code", target=" "), "needs `target`: a command"),
    (deterministic("exit_code", target="true", expected="zero"), "must be a whole number"),
    (deterministic("exit_code", target="true", expected=1.5), "must be a whole number"),
    (deterministic("exit_code", target="true", expected=True), "must be a whole number"),
    (deterministic("output_matches"), "needs `expected`"),
    (deterministic("output_matches", expected="("), "not a valid regular expression"),
    (deterministic("output_matches", expected=3), "needs `expected`"),
    (check(judge="llm"), "needs `checklist`"),
    (check(judge="llm", checklist="says hello"), "needs `checklist`"),
    (check(judge="llm", checklist=["ok", ""]), "needs `checklist`"),
])
def test_rejects_checks_that_could_only_fail_once_an_agent_had_run(folder, data, message):
    with pytest.raises(TaskError, match=message):
        load_tasks(write(folder, data))


def test_a_file_that_is_not_utf8_is_refused(folder):
    path = folder / "tasks.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(TaskError, match="cannot read"):
        load_tasks(path)


def test_every_problem_is_listed_by_task(folder):
    data = [{**TASK, "fixtures": ["missing.txt", "../outside.txt"], "prompt": 1},
            "text",
            {**TASK, "checks": [{"judge": "llm"}, {"judge": "deterministic"}]}]
    with pytest.raises(TaskError) as caught:
        load_tasks(write(folder, data))
    problems = caught.value.problems
    assert [p.split(":")[0] for p in problems] == [
        "task 1 (upload-single-file)", "task 1 (upload-single-file)",
        "task 1 (upload-single-file)", "task 2 is not an object",
        "task 3 (upload-single-file)", "task 3 (upload-single-file)",
        "task 3 (upload-single-file)"]
    assert any("duplicate task_id" in p for p in problems)
    assert all(p in str(caught.value) for p in problems)


@pytest.mark.parametrize("fields, expected", [
    ({"kind": "exit_code", "target": "false", "expected": 1}, "1"),  # YAML gives numbers
    ({"kind": "exit_code", "target": "true", "expected": None}, ""),
    ({"kind": "file_exists", "target": "a.txt", "expected": None, "checklist": None}, ""),
])
def test_accepts_a_number_or_nothing_where_text_goes(folder, fields, expected):
    [task] = load_tasks(write(folder, check(judge="deterministic", **fields)))
    assert task.checks[0].expected == expected and task.checks[0].checklist == []


def test_a_check_written_back_by_to_dict_loads_without_warnings(folder):
    llm = {"judge": "llm", "checklist": ["says hello"]}
    [task] = load_tasks(write(folder, [{**TASK, "checks": [TASK["checks"][0], llm]}]))
    found = read_tasks(write(folder, [{**task.to_dict(), "fixtures": ["files/a.txt"]}]))
    assert found.warnings == []


def test_warns_about_what_is_ignored(folder):
    data = {"notes": "x", "tasks": [
        {**{k: v for k, v in TASK.items() if k != "fixtures"}, "fixture": ["files/a.txt"],
         "checks": [{"judge": "llm", "checklist": ["x"], "target": "a.txt", "wieght": 2}]}]}
    warnings = read_tasks(write(folder, data)).warnings
    assert warnings == [
        "the file: unknown field 'notes' is ignored",
        "task 1 (upload-single-file): unknown field 'fixture' is ignored; did you mean "
        "`fixtures`?",
        "task 1 (upload-single-file), check 1: unknown field 'wieght' is ignored",
        "task 1 (upload-single-file), check 1: `target` is ignored by this check",
    ]
