"""Evaluation plans (plans.py): draft, validate, approve and run, and the /metaskill commands.

A fake model drafts, and a fake sandbox runs the `python3 -c` checks on this machine in a
temporary folder, so no model is called and no container runs.
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

import metaskill.evaluate as evaluate
import tools
from metaskill.evaluate import plans
from metaskill.evaluate.models import Report
from metaskill.evaluate.runner import AgentRun
from metaskill.evaluate.sandbox import CommandResult
from sandbox_helpers import FakeSandbox, FakeWorkspace
from test_runner import FakeAgent

CHECK = ("python3 -c 'import sys; sys.exit(0 if open(\"total.txt\").read().strip() == "
         "\"6\" else 1)'")


class LocalWorkspace(FakeWorkspace):
    def run_command(self, command, timeout, input=None):
        program, flag, code = shlex.split(command)
        assert (program, flag) == ("python3", "-c")
        done = subprocess.run([sys.executable, flag, code], cwd=self.path, capture_output=True,
                              text=True, timeout=timeout)
        return CommandResult(done.returncode, done.stdout, done.stderr)


class LocalSandbox(FakeSandbox):
    def check(self):
        return None

    def open(self, fixtures):
        workspace = LocalWorkspace(self)
        workspace.put(fixtures, workspace.root)
        self.opened.append(workspace)
        return workspace


def task(**changes):
    return {"task_id": "total", "description": "Add up a column",
            "prompt": "Write the sum of the numbers in data.csv to total.txt.",
            "files": [{"name": "data.csv", "content": "n\n1\n2\n3\n"}], "checks": [CHECK],
            "example": [{"name": "total.txt", "content": "6\n"}], **changes}


def draft(*tasks, comparable=True):
    return {"comparable": comparable, "reason": "r", "tasks": list(tasks)}


class Drafter:
    """ctx.llm: the given replies in order."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        return SimpleNamespace(provider="fake", model="drafter", text=json.dumps(reply),
                               parsed=None, content_type="text",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.fixture
def skills(tmp_path):
    made = []
    for name in ("adder", "summer"):
        folder = tmp_path / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"# {name}\n\nAdds up numbers.\n\nSECRET STEP: write the answer to total.txt.\n",
            encoding="utf-8")
        made.append(folder)
    return made


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "plans"
    monkeypatch.setattr(plans, "plans_root", lambda: folder)
    return folder


def configure(skills, *replies, **kwargs):
    llm = Drafter(*(replies or [draft(task())]))
    plan = plans.configure(skills, ctx=SimpleNamespace(llm=llm), sandbox=LocalSandbox(), **kwargs)
    return plan, llm


def approved(skills):
    plan, _ = configure(skills)
    return plans.approve(plan.id, sandbox=LocalSandbox())


def saved(root):
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


# --- configure -----------------------------------------------------------------------


def test_configure_saves_a_validated_draft(root, skills):
    plan, llm = configure(skills, label="sums")
    assert plans.PLAN_ID.fullmatch(plan.id) and plan.status == "draft"
    assert saved(root) == [plan.id]
    assert plan.data["label"] == "sums"
    assert [s["name"] for s in plan.data["skills"]] == ["adder", "summer"]
    assert (plan.folder / "fixtures/total/data.csv").read_text() == "n\n1\n2\n3\n"
    assert len(llm.calls) == 1
    shown = plans.summary(plan)
    assert CHECK in shown and "8 agent sessions" in shown and "draft" in shown


def sent(llm):
    return json.loads(llm.calls[0]["input"][0]["text"])


def test_the_drafter_is_given_each_skills_instructions(root, skills):
    (skills[0] / "SKILL.md").write_text(
        "# adder\n\nWrite total.txt.\n<!-- HIDDEN ORDER -->\n" + "x" * 5000, encoding="utf-8")
    _, llm = configure(skills)
    [first, second] = sent(llm)
    assert "Write total.txt." in first["instructions"]
    assert "HIDDEN ORDER" not in first["instructions"]
    assert first["instructions"].endswith("[cut]") and len(first["instructions"]) < 4100
    assert "SECRET STEP" in second["instructions"]
    told = llm.calls[0]["instructions"]
    assert "SECRET STEP" not in told and "names no rule, format" in told


def test_a_description_and_the_text_after_the_frontmatter_are_sent(root, tmp_path, monkeypatch):
    # Stands in for Hermes' YAML loader, which reading frontmatter needs.
    yaml = types.ModuleType("hermes_yaml")
    yaml.safe_load = lambda text: dict(line.split(": ", 1) for line in text.splitlines())
    monkeypatch.setitem(sys.modules, "hermes_yaml", yaml)
    folder = tmp_path / "described"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\ndescription: Adds numbers.\n---\n# Body\n",
                                     encoding="utf-8")
    _, llm = configure([folder])
    [skill] = sent(llm)
    assert skill["description"] == "Adds numbers." and skill["instructions"] == "# Body"


def test_a_note_is_part_of_the_instructions_and_kept(root, skills):
    plan, llm = configure(skills, note="  Test that blank cells count as zero. ")
    assert "Test that blank cells count as zero." in llm.calls[0]["instructions"]
    assert "blank cells" not in json.dumps(llm.calls[0]["input"])
    assert plan.data["note"] == "Test that blank cells count as zero."
    assert "Your note: Test that blank cells" in plans.summary(plan)


def test_without_a_note_none_is_sent_or_kept(root, skills):
    plan, llm = configure(skills)
    assert "asks the following" not in llm.calls[0]["instructions"] and "note" not in plan.data


def test_a_note_that_is_too_long_is_refused_before_any_call(root, skills):
    with pytest.raises(plans.PlanError, match="at most 600"):
        configure(skills, note="x" * 601)
    assert saved(root) == []


@pytest.mark.parametrize("bad", [
    task(checks=["python3 -c 'pass'"]),                        # passes with nothing done
    task(example=[{"name": "total.txt", "content": "7\n"}]),   # the example does not pass
    task(files=[{"name": "../escape", "content": ""}]),
    task(files=[{"name": "a/b", "content": ""}]),
    task(task_id="../x"),
    task(checks=[]),
    task(checks=["x" * 3000]),
    task(checks=[CHECK] * 5),
    task(checks=["true\r\nfalse"]),
    task(files=[{"name": "big", "content": "x" * 5000}]),
    task(prompt=""),
])
def test_a_bad_draft_is_asked_again_then_refused(root, skills, bad):
    with pytest.raises(plans.PlanError, match="no plan was saved"):
        configure(skills, draft(bad), draft(bad))
    assert saved(root) == []
    assert not (root.parent / "escape").exists()


def test_a_check_may_run_over_several_lines_and_each_line_is_shown(root, skills):
    command = ("python3 -c '\nimport sys\n"
               "sys.exit(0 if open(\"total.txt\").read().strip() == \"6\" else 1)\n'")
    plan, _ = configure(skills, draft(task(checks=[command])))
    shown = plans.summary(plan).split("\n")
    assert "    $ python3 -c '" in shown and "    | import sys" in shown
    plans.approve(plan.id, sandbox=LocalSandbox())


@pytest.mark.parametrize("bad, why", [
    (task(checks=[CHECK] * 5), "has 5 checks; at most 4"),
    (task(checks=[]), "at least one check"),
    (task(checks=["x" * 3000]), "3000 characters; at most 2000"),
    (task(checks=["a\x1bb"]), "control character"),
])
def test_the_reason_a_check_is_refused_is_named(root, skills, bad, why):
    with pytest.raises(plans.PlanError, match=why):
        configure(skills, draft(bad), draft(bad))


def test_the_drafter_is_told_the_limits(root, skills):
    _, llm = configure(skills)
    sent = llm.calls[0]["instructions"]
    assert "1 to 4 checks" in sent and "2000 characters" in sent and "4000 characters" in sent


def test_the_drafter_is_told_when_skills_are_comparable(root, skills):
    _, llm = configure(skills)
    sent = llm.calls[0]["instructions"]
    assert "only when the skills do clearly unrelated jobs" in sent


def test_the_second_draft_is_told_why_the_first_failed(root, skills):
    plan, llm = configure(skills, draft(task(checks=["python3 -c 'pass'"])), draft(task()))
    assert plan.status == "draft"
    assert "pass before any work is done" in llm.calls[1]["instructions"]


def test_skills_without_a_common_purpose_make_no_plan(root, skills):
    with pytest.raises(plans.PlanError, match="no common purpose"):
        configure(skills, draft(comparable=False))
    assert saved(root) == []


def test_configure_needs_a_model_and_a_sandbox(root, skills):
    with pytest.raises(plans.PlanError, match="needs a model"):
        plans.configure(skills, ctx=SimpleNamespace(), sandbox=LocalSandbox())
    with pytest.raises(plans.PlanError, match="Docker is off"):
        plans.configure(skills, ctx=SimpleNamespace(llm=Drafter()),
                        sandbox=FakeSandbox(reason="Docker is off"))


# --- approve and ready ---------------------------------------------------------------


def test_a_draft_is_not_run(root, skills):
    plan, _ = configure(skills)
    with pytest.raises(plans.PlanError, match=f"approve {plan.id}"):
        plans.ready(plan.id, "rank")


def test_an_approved_plan_is_ready(root, skills):
    plan = approved(skills)
    ready, warnings = plans.ready(plan.id, "rank")
    assert ready.status == "approved" and warnings == []
    with pytest.raises(plans.PlanError, match="use /metaskill rank"):
        plans.ready(plan.id, "evaluate")


def test_a_one_skill_plan_is_for_evaluate(root, skills):
    plan = approved(skills[:1])
    plans.ready(plan.id, "evaluate")
    with pytest.raises(plans.PlanError, match="use /metaskill evaluate"):
        plans.ready(plan.id, "rank")


@pytest.mark.parametrize("name", [
    "tasks.json", "examples.json", "config.json", "fixtures/total/data.csv", "fixtures/total/new",
])
def test_a_plan_changed_after_approval_needs_approval_again(root, skills, name):
    plan = approved(skills)
    path = plan.folder / name
    path.write_text((path.read_text() if path.exists() else "") + "\n", encoding="utf-8")
    with pytest.raises(plans.PlanError, match="changed after it was approved"):
        plans.ready(plan.id, "rank")
    plans.approve(plan.id, sandbox=LocalSandbox())
    plans.ready(plan.id, "rank")


def test_approve_validates_the_files_as_they_are_now(root, skills):
    plan, _ = configure(skills)
    tasks = plan.folder / "tasks.json"
    data = json.loads(tasks.read_text())
    data["tasks"][0]["checks"][0]["target"] = "python3 -c 'pass'"
    tasks.write_text(json.dumps(data))
    with pytest.raises(plans.PlanError, match="was not approved"):
        plans.approve(plan.id, sandbox=LocalSandbox())
    assert plans.load(plan.id).status == "draft"


def test_a_changed_skill_is_a_warning_and_a_missing_one_an_error(root, skills):
    plan = approved(skills)
    (skills[0] / "SKILL.md").write_text("# changed\n", encoding="utf-8")
    _, [warning] = plans.ready(plan.id, "rank")
    assert "adder has changed" in warning
    (skills[1] / "SKILL.md").unlink()
    skills[1].rmdir()
    with pytest.raises(plans.PlanError, match="no longer at"):
        plans.ready(plan.id, "rank")


@pytest.mark.parametrize("plan_id", ["plan-zzzzzz", "../plan-000000", "plan-000000/..", "x"])
def test_only_a_plan_id_names_a_plan(root, plan_id):
    with pytest.raises(plans.PlanError, match="not a plan ID"):
        plans.load(plan_id)


def test_unknown_and_broken_plans(root):
    with pytest.raises(plans.PlanError, match="there is no plan"):
        plans.load("plan-000000")
    (root / "plan-000000").mkdir(parents=True)
    (root / "plan-000000/plan.json").write_text("[]")
    with pytest.raises(plans.PlanError, match="cannot be used"):
        plans.load("plan-000000")
    assert plans.all_plans() == []


# --- The /metaskill commands ---------------------------------------------------------


@pytest.fixture
def command(monkeypatch, tmp_path, root):
    """The command handler, with the real pipeline and a fake agent and sandbox."""
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: LocalSandbox())
    monkeypatch.setattr(plans, "_ready_sandbox", lambda sandbox, config: LocalSandbox())

    def act(prompt, skill, workdir):
        if skill is not None:
            (workdir / "total.txt").write_text("6\n")
        return AgentRun(output="done", input_tokens=10, output_tokens=1, llm_calls=1)
    monkeypatch.setattr(evaluate, "_make_agent", lambda config: FakeAgent(act))
    ctx = SimpleNamespace(llm=Drafter(draft(task())), session_cwd=lambda: str(tmp_path),
                          inject_message=lambda text: pytest.fail(f"sent to the agent: {text}"))
    return tools.make_command_handler(ctx)


def test_configure_approve_and_rank(command, skills, root, tmp_path):
    assert "No evaluation plans" in command("plans")
    shown = command("configure skills/adder, skills/summer --name my-sums")
    [plan_id] = saved(root)
    assert plan_id in shown and CHECK in shown and "not approved yet" in shown
    assert "is a draft" in command(f"rank {plan_id}")
    assert "/metaskill rank my-sums" in command(f"approve {plan_id}")
    listed = command("plans")
    assert plan_id in listed and '"my-sums"' in listed and "approved" in listed

    out = command(f"rank {plan_id}")
    assert out.startswith(f"Plan {plan_id}") and "adder" in out and "summer" in out
    [report_file] = (tmp_path / "evaluation-reports").glob("*/ranked-evaluation.json")
    report = Report.from_dict(json.loads(report_file.read_text()))
    assert report.meta.plan_id == plan_id and report.meta.runs_per_task == 2
    assert all(len(s.tests) == 4 for s in report.skills)
    assert f"- Plan: {plan_id}" in report_file.with_suffix(".md").read_text()


@pytest.mark.parametrize("options", [
    '--name sums --note "Check the -- blank cells, please"',
    '--note Check the blank cells --name sums',
])
def test_configure_takes_a_name_and_a_note_in_either_order(command, skills, root, options):
    out = command(f"configure skills/adder, skills/summer {options}")
    [plan_id] = saved(root)
    data = plans.load(plan_id).data
    assert data["label"] == "sums" and "blank cells" in data["note"]
    assert "Your note:" in out


def test_a_plan_is_approved_and_run_by_its_name(command, skills, root, tmp_path):
    shown = command("configure skills/adder, skills/summer --name Sales_demo")
    [plan_id] = saved(root)
    assert "/metaskill approve Sales_demo" in shown
    assert "is a draft" in command("rank sales_demo")
    assert "/metaskill rank Sales_demo" in command("approve SALES_DEMO")
    assert "takes nothing else" in command("rank Sales_demo --tasks t.json")
    out = command("rank Sales_demo")
    assert out.startswith(f"Plan {plan_id}") and "adder" in out
    assert "use /metaskill rank" in command("evaluate Sales_demo")


def test_a_name_that_is_also_a_skill_is_not_guessed(command, skills, root, tmp_path):
    command("configure skills/adder --name demo")
    [plan_id] = saved(root)
    (tmp_path / "demo").mkdir()
    out = command("evaluate demo")
    assert "nothing was run" in out and plan_id in out and "./demo" in out


def test_a_list_of_skills_is_not_read_as_a_plan(command, skills, root):
    command("configure skills/adder --name demo")
    assert "No skill found" in command("rank demo, skills/adder")


@pytest.mark.parametrize("name", ["two words", "plan-abc", "PLAN-1", "-x", "a/b", "x" * 41, "a.b"])
def test_a_name_that_commands_could_not_take_is_refused_before_drafting(root, skills, name):
    with pytest.raises(plans.PlanError, match="a plan name is one word"):
        configure(skills, draft(task()), label=name)
    assert saved(root) == []


def test_a_name_is_used_once_in_any_case(root, skills):
    first, _ = configure(skills, label="demo")
    llm = Drafter()
    with pytest.raises(plans.PlanError, match=f"already used by {first.id}"):
        plans.configure(skills, ctx=SimpleNamespace(llm=llm), sandbox=LocalSandbox(), label="Demo")
    assert llm.calls == [] and saved(root) == [first.id]
    assert plans.resolve("DEMO") == first.id and plans.resolve("other") is None
    assert plans.resolve(first.id) == first.id


def test_two_plans_edited_to_share_a_name_need_the_id(root, skills):
    first, _ = configure(skills, label="a")
    second, _ = configure(skills, label="b")
    second.data["label"] = "a"
    (second.folder / "plan.json").write_text(json.dumps(second.data))
    with pytest.raises(plans.PlanError, match="use the ID"):
        plans.resolve("a")


@pytest.mark.parametrize("request_text, expected", [
    ("configure", "Usage: /metaskill configure"),
    ("configure --name x", "Usage: /metaskill configure"),
    ("configure a --note x --note y", "Usage: /metaskill configure"),
    ("configure a --force", "Usage: /metaskill configure"),
    ("configure nothing-here", "No skill found"),
    ("approve", "Usage: /metaskill approve"),
    ("approve plan-000000", "there is no plan"),
    ("approve nothing", "there is no plan nothing"),
    ("plans extra", "Usage: /metaskill plans"),
    ("rank plan-000000", "there is no plan"),
    ("rank plan-000000 --tasks t.json", "takes nothing else"),
    ("evaluate plan-000000 --config c.json", "takes nothing else"),
])
def test_command_mistakes_are_explained(command, request_text, expected):
    assert expected in command(request_text)


def test_a_skill_that_looks_like_a_plan_id_is_not_guessed(command, tmp_path):
    (tmp_path / "plan-000000").mkdir()
    assert "nothing was run" in command("evaluate plan-000000")


def test_the_model_tool_cannot_approve(root, skills):
    plan, _ = configure(skills)
    for operation in ("approve", "configure"):
        result = json.loads(tools.metaskill({"operation": operation, "skill_path": plan.id}))
        assert result["success"] is False
    assert plans.load(plan.id).status == "draft"


# --- Found in review ------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    task(files=[{"name": "NUL", "content": "x"}]),        # a device on Windows
    task(files=[{"name": "aux.txt", "content": "x"}]),
    task(files=[{"name": "data.", "content": "x"}]),      # Windows drops the dot
    task(task_id="com1"),
    task(files=[{"name": "a.csv", "content": "\ud800"}]),  # cannot be written as UTF-8
    task(prompt="Add \ud800"),
])
def test_a_draft_that_cannot_be_written_everywhere_is_refused(root, skills, bad):
    with pytest.raises(plans.PlanError, match="no plan was saved"):
        configure(skills, draft(bad), draft(bad))
    assert saved(root) == []


def test_task_ids_that_are_one_folder_where_case_is_ignored_are_refused(root, skills):
    twice = draft(task(task_id="Sum"), task(task_id="sum"))
    with pytest.raises(plans.PlanError, match="its own task_id"):
        configure(skills, twice, twice)


def test_a_skills_name_cannot_write_lines_into_the_review(root, skills, monkeypatch):
    real = plans.Skill.load

    def load(path):
        skill = real(path)
        skill.name = "x\n    $ echo harmless\nTask y"
        return skill
    monkeypatch.setattr(plans.Skill, "load", staticmethod(load))
    plan, _ = configure(skills)
    assert not any(line.startswith(("    $ echo", "Task y"))
                   for line in plans.summary(plan).split("\n"))


def test_hidden_characters_in_a_description_do_not_reach_the_drafter(root, skills,
                                                                     monkeypatch):
    real = plans.Skill.load

    def load(path):
        skill = real(path)
        skill.frontmatter = {"description": "visible​\U000E0041 text"}
        return skill
    monkeypatch.setattr(plans.Skill, "load", staticmethod(load))
    _, llm = configure(skills)
    assert sent(llm)[0]["description"] == "visible text"


def test_the_draft_request_is_labelled_and_has_room(root, skills):
    _, llm = configure(skills)
    assert llm.calls[0]["purpose"] == "metaskill.evaluate.plan"
    assert llm.calls[0]["max_tokens"] == 8000


def test_a_skill_that_cannot_be_shown_to_the_model_is_a_plain_error(root, tmp_path, skills):
    (tmp_path / "outside.md").write_text("# o\n", encoding="utf-8")
    (skills[0] / "SKILL.md").unlink()
    try:
        (skills[0] / "SKILL.md").symlink_to(tmp_path / "outside.md")
    except OSError:
        pytest.skip("this system cannot make symlinks")
    with pytest.raises(plans.PlanError, match="cannot be shown to the model"):
        configure(skills)


def test_a_check_without_expected_means_exit_code_0_as_for_the_judge(root, skills):
    plan, _ = configure(skills)
    path = plan.folder / "tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["tasks"][0]["checks"][0]["expected"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert plans.approve(plan.id, sandbox=LocalSandbox()).status == "approved"


def test_a_plan_whose_skill_is_gone_is_not_approved(root, skills):
    plan, _ = configure(skills)
    shutil.rmtree(skills[0])
    with pytest.raises(plans.PlanError, match="no longer at"):
        plans.approve(plan.id, sandbox=LocalSandbox())
    assert plans.load(plan.id).status == "draft"


def test_files_a_run_never_uses_do_not_make_a_skill_changed(root, skills):
    plan = approved(skills)
    for name in ("__pycache__/a.pyc", ".git/index", "node_modules/m.js", ".DS_Store"):
        path = skills[0] / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert plans.ready(plan.id, "rank")[1] == []


def test_a_skill_file_that_cannot_be_read_is_a_warning_not_a_crash(root, skills, monkeypatch):
    (skills[0] / "notes.txt").write_text("x", encoding="utf-8")
    plan = approved(skills)
    real = Path.open

    def open_(self, *args, **kwargs):
        if self.name == "notes.txt":
            raise PermissionError("no")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_)
    assert "changed" in plans.ready(plan.id, "rank")[1][0]


def test_configure_takes_a_folder_with_two_dashes_in_its_name(command, tmp_path):
    folder = tmp_path / "my--skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# s\n", encoding="utf-8")
    assert "Usage:" not in command("configure my--skill")
    assert "Usage:" in command("configure my--skill --bogus")
