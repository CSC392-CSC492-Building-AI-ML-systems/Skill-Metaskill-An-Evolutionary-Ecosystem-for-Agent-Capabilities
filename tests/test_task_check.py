"""`/metaskill tasks check`, and the example in examples/tasks.

Nothing here calls a model or starts a container. In the evaluate test a scripted agent
does the work and the example's `python3` checks run in a temporary folder on this machine.
"""

import json
import shlex
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools
from metaskill.evaluate import check_task_file, evaluate_skill
from metaskill.evaluate.models import Report, Status, Dimension
from metaskill.evaluate.runner import AgentRun
from metaskill.evaluate.sandbox import CommandResult, DockerSandbox
from sandbox_helpers import FakeSandbox, FakeWorkspace
from test_runner import FakeAgent

EXAMPLE = Path(__file__).resolve().parent.parent / "examples/tasks/expense-summary"

TASKS = [
    {"task_id": "one", "description": "d", "prompt": "p",
     "checks": [{"judge": "deterministic", "kind": "file_exists", "target": "a.txt"}]},
    {"task_id": "two", "description": "d", "prompt": "p",
     "checks": [{"judge": "llm", "checklist": ["says hello"]}]},
]


@pytest.fixture
def command(tmp_path):
    """`/metaskill <request>` in a session whose folder is tmp_path."""
    handler = tools.make_command_handler(SimpleNamespace(session_cwd=lambda: str(tmp_path)))
    return handler


@pytest.fixture
def nothing_runs(monkeypatch):
    """Fails the test if a sandbox is opened or asked about, or a program is started."""
    def refuse(*args, **kwargs):
        raise AssertionError("tasks check must not run anything")

    for name in ("open", "check", "unavailable", "_docker", "_stream"):
        monkeypatch.setattr(DockerSandbox, name, refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)


def write(folder, data, name="tasks.json"):
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_a_valid_file_reports_its_tasks_and_sessions(tmp_path, command, nothing_runs):
    write(tmp_path, TASKS)
    reply = command("tasks check tasks.json")
    assert "tasks.json: OK, 2 task(s): one, two" in reply
    assert "Agent sessions per skill: 12 (2 task(s) x 3 run(s)" in reply
    assert "llm checks, per skill: up to 6" in reply
    assert reply.endswith("Nothing was run.")


@pytest.mark.parametrize("request_text", [
    "tasks check tasks.json --config config.json",
    'tasks check "tasks.json" --config "config.json"',
])
def test_the_config_sets_the_runs(tmp_path, command, nothing_runs, request_text):
    write(tmp_path, TASKS)
    write(tmp_path, {"runs_per_task": 5}, "config.json")
    assert "Agent sessions per skill: 20" in command(request_text)


def test_a_path_with_spaces(tmp_path, command, nothing_runs):
    (tmp_path / "my evals").mkdir()
    write(tmp_path / "my evals", TASKS)
    assert "OK, 2 task(s)" in command('tasks check "my evals/tasks.json"')


def test_an_invalid_file_names_each_task_and_problem(tmp_path, command, nothing_runs):
    write(tmp_path, [
        {**TASKS[0], "fixtures": ["../x"]},
        {k: v for k, v in TASKS[1].items() if k != "prompt"},
        TASKS[0],
    ])
    reply = command("tasks check tasks.json")
    assert "cannot be used (3 problem(s))" in reply
    assert "- task 1 (one): fixture ../x is outside the task file's folder" in reply
    assert "- task 2 (two): missing field `prompt`" in reply
    assert "- task 3 (one): duplicate task_id 'one'" in reply
    assert "OK" not in reply


def test_warnings_are_shown(tmp_path, command, nothing_runs):
    write(tmp_path, [{**TASKS[0], "fixture": []}])
    assert "did you mean `fixtures`?" in command("tasks check tasks.json")


@pytest.mark.parametrize("request_text", [
    "tasks", "tasks check", "tasks run tasks.json", "tasks check a.json --tasks b.json",
])
def test_another_shape_gets_the_usage(command, nothing_runs, request_text):
    assert command(request_text) == tools.TASKS_USAGE


@pytest.mark.parametrize("request_text, missing", [
    ("tasks check nope.json", "task file"),
    ("tasks check tasks.json --config nope.json", "config file"),
])
def test_a_missing_file(tmp_path, command, nothing_runs, request_text, missing):
    write(tmp_path, TASKS)
    assert f"the {missing} does not exist" in command(request_text)


def test_a_bad_config(tmp_path, command, nothing_runs):
    write(tmp_path, TASKS)
    write(tmp_path, {"runs_per_task": 0}, "config.json")
    reply = command("tasks check tasks.json --config config.json")
    assert "config_path is not a valid evaluation config" in reply


def test_evaluate_reports_the_files_problems_and_warnings(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s/SKILL.md").write_text("# s\n", encoding="utf-8")
    write(tmp_path, [{**TASKS[0], "checks": "file_exists"}])
    result = evaluate_skill(tmp_path / "s", tasks_path=tmp_path / "tasks.json",
                            output_path=tmp_path / "out")
    assert result["status"] == "error"
    assert "tasks check" in result["error"] and "non-empty list" in result["error"]


# --- The example ----------------------------------------------------------------------


def test_the_example_passes_the_check(nothing_runs):
    result = check_task_file(EXAMPLE / "tasks.json")
    assert result["status"] == "ok" and result["warnings"] == []
    assert result["task_ids"] == ["march", "all-refunded"]
    assert result["sessions_per_skill"] == 12 and result["llm_judge_calls_per_skill"] == 6


class LocalWorkspace(FakeWorkspace):
    """Runs a `python3 -I -c ...` check in the workspace folder with this interpreter."""

    def run_command(self, command, timeout, input=None):
        program, *flags, code = shlex.split(command)
        assert (program, flags) == ("python3", ["-I", "-c"])
        done = subprocess.run([sys.executable, *flags, code], cwd=self.path,
                              capture_output=True, text=True, timeout=timeout)
        return CommandResult(done.returncode, done.stdout, done.stderr)


class LocalSandbox(FakeSandbox):
    image, pull_command = "", ""

    def check(self):
        return None

    def open(self, fixtures):
        workspace = LocalWorkspace(self)
        workspace.put(fixtures, workspace.root)
        self.opened.append(workspace)
        return workspace


def summarize(workdir, skip_refunds=True):
    totals = {}
    for line in (workdir / "expenses.csv").read_text(encoding="utf-8").split()[1:]:
        _, category, amount, status = line.split(",")
        if status != "refunded" or not skip_refunds:
            totals[category] = totals.get(category, 0) + round(float(amount) * 100)
    by = {c: v / 100 for c, v in totals.items()}
    summary = {"total": sum(totals.values()) / 100, "by_category": by,
               "largest": max(by, key=by.get) if by else None}
    (workdir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


ANSWER = "The category that cost the most is travel, at 308.75."


def agent(skip_refunds_without_the_skill):
    def act(prompt, skill, workdir):
        summary = summarize(workdir, skill is not None or skip_refunds_without_the_skill)
        output = ANSWER if summary["total"] else "TOTAL=0.00"
        return AgentRun(output=output, input_tokens=100, output_tokens=10, llm_calls=2,
                        tool_calls=1)
    return act


class Judge:
    def complete_structured(self, **kwargs):
        items = [{"item": n, "met": True, "reason": "r", "quote": ANSWER} for n in (1, 2)]
        return SimpleNamespace(provider="fake", model="judge", parsed=None,
                               content_type="text", text=json.dumps({"items": items}),
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.mark.parametrize("baseline_right, lift", [(True, 0.0), (False, 1.0)])
def test_the_example_runs_through_evaluate(tmp_path, monkeypatch, baseline_right, lift):
    loader = types.ModuleType("hermes_yaml")
    loader.safe_load = lambda text: dict(line.split(": ", 1) for line in text.splitlines())
    monkeypatch.setitem(sys.modules, "hermes_yaml", loader)
    shutil.copytree(EXAMPLE, tmp_path / "example")
    result = evaluate_skill(
        tmp_path / "example/skill", tasks_path=tmp_path / "example/tasks.json",
        output_path=tmp_path / "out", ctx=SimpleNamespace(llm=Judge()),
        agent=FakeAgent(agent(baseline_right)), sandbox=LocalSandbox())
    assert result["task_runs"] == {"tasks": 2, "sessions": 12, "errors": 0}
    path = next(p for p in result["artifacts"] if p.endswith(".json"))
    skill = Report.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))).skills[0]
    assert skill.dimensions[Dimension.QUALITY].status is Status.OK
    with_skill = [t for t in skill.tests if t.variant.value == "with_skill"]
    assert all(t.passed for t in with_skill), [t.reason for t in with_skill]
    # A baseline that counts refunded rows fails both tasks' checks.
    assert skill.lift == lift
