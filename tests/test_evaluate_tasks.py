"""evaluate_skill and rank_skills with a task file (tasks_path): the runner wired in.

A fake agent and a fake sandbox stand in for Hermes and Docker, so nothing here calls a
model or runs a container. tests/test_hermes_agent.py covers the real agent.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import metaskill.evaluate as evaluate
from metaskill.evaluate import evaluate_skill, rank_skills
from metaskill.evaluate.hermes_agent import HermesAgentUnavailable
from metaskill.evaluate.models import Dimension, EvaluationConfig, Report, Status, Variant
from metaskill.evaluate.runner import AgentRun
from metaskill.evaluate.sandbox import Problem
from metaskill.evaluate.sandbox import RunFailed
from test_runner import FakeAgent, FakeLlm, FakeSandbox, writes_report_with_skill
from test_evaluate import placeholder  # noqa: F401  (fixture: scores a dimension with a test module)

RUNS = 2  # runs_per_task in these tests


class ReadySandbox(FakeSandbox):
    """FakeSandbox with the check dependencies.py makes: Docker ready, or `problem`."""

    image, pull_command = "python:3.12-slim", "docker pull python:3.12-slim"

    def __init__(self, problem=None, **kwargs):
        super().__init__(**kwargs)
        self.problem = problem

    def check(self):
        return self.problem


def cheap_with_skill(prompt, skill, workdir):
    """With the skill the agent is cheaper than without: efficiency above 0.5."""
    if skill is not None:
        return AgentRun(output="done", input_tokens=100, output_tokens=10, llm_calls=2, tool_calls=1)
    return AgentRun(output="done", input_tokens=400, output_tokens=40, llm_calls=6, tool_calls=4)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Swaps the agent and sandbox evaluate builds for fakes, and writes a task file."""
    made = SimpleNamespace(agents=[], sandbox=ReadySandbox(), agent_act=cheap_with_skill)

    def make_agent(config):
        agent = FakeAgent(made.agent_act)
        agent.models_used = {"fake-model"}
        made.agents.append(agent)
        return agent

    monkeypatch.setattr(evaluate, "_make_agent", make_agent)
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: made.sandbox)
    tasks = tmp_path / "evals"
    tasks.mkdir()
    (tasks / "data.csv").write_text("a,b\n1,2\n")
    made.tasks = tasks / "tasks.json"
    made.tasks.write_text(json.dumps({"tasks": [
        {"task_id": f"t{i}", "description": "d", "prompt": f"task {i}", "fixtures": ["data.csv"],
         "checks": [{"judge": "deterministic", "kind": "exit_code", "target": "true", "expected": "0"}]}
        for i in (1, 2)]}))
    made.config = tmp_path / "config.json"
    made.config.write_text(json.dumps({"runs_per_task": RUNS}))
    return made


def make_skill(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Skill\n\nSummarize the file.\n", encoding="utf-8")
    return folder


def report(result):
    path = next(p for p in result["artifacts"] if p.endswith(".json"))
    return Report.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --- Without tasks nothing runs ----------------------------------------------------------


def test_without_tasks_no_agent_is_made_and_task_dimensions_are_skipped(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out")
    assert harness.agents == [] and harness.sandbox.opened == []
    assert "task_runs" not in result
    skill = report(result).skills[0]
    efficiency = skill.dimensions[Dimension.EFFICIENCY]
    assert efficiency.status is Status.SKIPPED and "no tasks were given" in efficiency.detail
    assert skill.dimensions[Dimension.SAFETY].status is not Status.SKIPPED
    assert skill.tests == [] and report(result).meta.agent_model == ""


# --- With tasks ---------------------------------------------------------------------------


def test_tasks_run_once_per_skill_and_efficiency_scores_them(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    [agent] = harness.agents
    assert len(agent.calls) == 2 * RUNS * 2  # tasks x runs x variants
    assert result["task_runs"] == {"tasks": 2, "sessions": 2 * RUNS * 2, "errors": 0}
    skill = report(result).skills[0]
    efficiency = skill.dimensions[Dimension.EFFICIENCY]
    assert efficiency.status is Status.OK and skill.scores.efficiency > 0.5
    assert len(skill.tests) == 2 * RUNS * 2
    assert {t.variant for t in skill.tests} == {Variant.WITH_SKILL, Variant.BASELINE}
    # Both variants pass every run: Quality scores the same runs, and its lift is 0, not None.
    assert skill.dimensions[Dimension.QUALITY].status is Status.OK and skill.scores.quality == 0.75
    assert skill.lift == 0.0
    assert report(result).meta.agent_model == "fake-model"
    # Robustness scores the same runs: every with-skill run is identical, so it is 1.
    robustness = skill.dimensions[Dimension.ROBUSTNESS]
    assert robustness.status is Status.OK and skill.scores.robustness == 1.0
    assert f"2 task(s), {RUNS} runs each" in robustness.reason


def test_run_logs_go_into_this_calls_report_folder(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    run_dir = Path(result["artifacts"][0]).parent
    logs = sorted(p.name for p in (run_dir / "skill-01" / "logs").iterdir())
    assert len(logs) == 2 * RUNS * 2 and "t1-with_skill-r1.txt" in logs


def readable(result):
    return Path(next(p for p in result["artifacts"] if p.endswith(".md"))).read_text(encoding="utf-8")


def test_the_markdown_report_shows_the_task_table_for_a_run_with_tasks(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    text = readable(result)
    assert "### Task results" in text and "Logs: skill-01/logs/ in this report's folder" in text
    assert f"| t1 | with_skill | {RUNS}/{RUNS} (100%) | 110 | 2.0 | 1.0 | unchanged |" in text
    assert f"| t2 | baseline | {RUNS}/{RUNS} (100%) | 440 | 6.0 | 4.0 | - |" in text
    assert "could not be made" not in text.split("### Task results", 1)[1].split("### ", 2)[1]


def test_the_markdown_report_has_no_task_section_without_tasks(tmp_path, harness):
    assert "Task results" not in readable(evaluate_skill(make_skill(tmp_path, "s"),
                                                         output_path=tmp_path / "out"))


def test_rank_runs_every_skill_on_the_same_tasks(tmp_path, harness):
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    [agent] = harness.agents  # one agent serves the whole call
    by_skill = {}
    for call in agent.calls:
        by_skill.setdefault(call.skill, 0)
        by_skill[call.skill] += 1
    assert by_skill == {"a": 2 * RUNS, "b": 2 * RUNS, None: 2 * 2 * RUNS}  # None: baselines
    assert result["task_runs"]["sessions"] == 2 * 2 * RUNS * 2


def test_skills_with_the_same_name_keep_their_own_logs(tmp_path, harness, monkeypatch):
    # A skill and its generalized copy share a frontmatter name. Logs were kept by name,
    # so the second skill's runs replaced the first's, and the first's results pointed at
    # the other skill's sessions. Names are set through Skill.load: frontmatter needs Hermes.
    real_load = evaluate.Skill.load

    def load(path):
        skill = real_load(path)
        skill.name = "same"
        return skill
    monkeypatch.setattr(evaluate.Skill, "load", staticmethod(load))
    harness.agent_act = lambda prompt, skill, workdir: AgentRun(
        output=f"from {Path(skill.path).name if skill else 'none'}", input_tokens=1, llm_calls=1)
    result = rank_skills([make_skill(tmp_path, "original"), make_skill(tmp_path, "copy")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    run_dir = Path(result["artifacts"][0]).parent
    logs = {folder: (run_dir / folder / "logs" / "t1-with_skill-r1.txt").read_text(encoding="utf-8")
            for folder in ("skill-01", "skill-02")}
    assert "from original" in logs["skill-01"] and "from copy" in logs["skill-02"]
    # Each result names the log of its own session.
    for skill in report(result).skills:
        [test] = [t for t in skill.tests if (t.task_id, t.variant, t.run) == ("t1", Variant.WITH_SKILL, 1)]
        folder = test.output.split("(full log: ")[1].split("/")[0]
        assert test.output.split(" (full log")[0] in logs[folder]
    assert any("more than one skill is named same" in w for w in result["warnings"])
    # The Markdown points each skill at the folder its runs went to, not at "same" twice.
    [markdown] = [a for a in result["artifacts"] if str(a).endswith(".md")]
    text = Path(markdown).read_text(encoding="utf-8")
    assert text.count("Logs: skill-01/logs/") == 1 and text.count("Logs: skill-02/logs/") == 1
    assert {s.log_folder for s in report(result).skills} == {"skill-01", "skill-02"}


def test_log_folders_are_numbered_in_the_order_given():
    assert [evaluate._log_folder(n) for n in (1, 2, 10, 100)] \
        == ["skill-01", "skill-02", "skill-10", "skill-100"]


def test_a_relative_tasks_path_is_the_users_session_folder(tmp_path, harness):
    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path="out", tasks_path="evals/tasks.json", ctx=ctx)
    assert result["task_runs"]["sessions"] == 2 * RUNS * 2


# --- Before anything runs -------------------------------------------------------------------


def test_a_bad_task_file_is_an_error_before_anything_runs(tmp_path, harness):
    harness.tasks.write_text(json.dumps({"tasks": [{"task_id": "x"}]}))
    result = evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out",
                            tasks_path=harness.tasks)
    assert result["status"] == "error" and "tasks_path is not a usable task file" in result["error"]
    assert harness.agents == [] and not (tmp_path / "out").exists()


def test_missing_docker_is_an_error_with_dependencies_and_nothing_runs(tmp_path, harness):
    harness.sandbox = ReadySandbox(problem=(Problem.NOT_RUNNING, "Docker is not usable"))
    result = evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out",
                            tasks_path=harness.tasks)
    assert result["status"] == "error" and "dependencies" in result["error"]
    assert any(d["package"] == "Docker" and d["required"] for d in result["dependencies"])
    assert harness.agents == [] and not (tmp_path / "out").exists()


# --- When runs cannot be made ----------------------------------------------------------------


def test_runs_the_runner_cannot_make_are_errors_not_scores(tmp_path, harness):
    def unavailable(prompt, skill, workdir):
        raise HermesAgentUnavailable("Hermes resolves the terminal backend to 'local'")

    harness.agent_act = unavailable
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert result["task_runs"] == {"tasks": 2, "sessions": 0, "errors": 2 * RUNS * 2}
    # The response names the kind only; the message (it can hold local paths) is in the report.
    assert any("could not be made (HermesAgentUnavailable)" in w for w in result["warnings"])
    assert not any("terminal backend" in w for w in result["warnings"])
    skill = report(result).skills[0]
    efficiency = skill.dimensions[Dimension.EFFICIENCY]
    assert efficiency.status is Status.ERROR and "no task run completed" in efficiency.detail
    # Quality cannot compare without runs, so lift stays None rather than an invented 0.
    assert skill.dimensions[Dimension.QUALITY].status is Status.ERROR and skill.lift is None
    assert "resolves the terminal backend" in efficiency.detail
    # Robustness has no runs to compare either: an error naming the cause, not a skip.
    robustness = skill.dimensions[Dimension.ROBUSTNESS]
    assert robustness.status is Status.ERROR and skill.scores.robustness is None
    assert "resolves the terminal backend" in robustness.detail
    assert skill.dimensions[Dimension.SAFETY].status is not Status.SKIPPED  # still scored


def test_an_agent_failing_a_task_is_a_scored_failure_not_an_error(tmp_path, harness):
    def fails_with_skill(prompt, skill, workdir):
        if skill is not None:
            raise RuntimeError("model said no")
        return AgentRun(output="done", input_tokens=10, llm_calls=1, tool_calls=0)

    harness.agent_act = fails_with_skill
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert result["task_runs"]["errors"] == 0
    skill = report(result).skills[0]
    with_skill = [t for t in skill.tests if t.variant is Variant.WITH_SKILL]
    assert with_skill and not any(t.passed for t in with_skill)
    # Every with-skill run failed, so efficiency has nothing to credit (success-gated): 0.
    assert skill.dimensions[Dimension.EFFICIENCY].status is Status.OK
    assert skill.scores.efficiency == 0.0


def test_a_lone_surrogate_in_an_agents_output_does_not_stop_the_report(
        tmp_path, harness, monkeypatch):
    # The report quotes what a check matched.
    harness.tasks.write_text(json.dumps({"tasks": [{
        "task_id": "t", "description": "d", "prompt": "p",
        "checks": [{"judge": "deterministic", "kind": "output_matches",
                    "expected": "done .*"}]}]}))
    use(harness, monkeypatch, FakeAgent(lambda *a: AgentRun(output="done \ud800 end")))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert "done" in readable(result)  # written, not a UnicodeEncodeError
    assert len(report(result).skills[0].tests) == 2 * RUNS


# Names that broke the report or a folder when the name was the folder (and the pairs that
# shared one on Windows or macOS). Set through Skill.load: frontmatter needs Hermes.
HOSTILE_NAMES = [
    "ranked-evaluation.json", "ranked-evaluation.md", "Ranked-Evaluation.JSON",
    "NUL", "CON", "PRN", "AUX", "COM1", "LPT9", "nul.txt", "foo", "foo.", "foo ", "Foo",
    "../escape", "/etc", "a\\b", "x" * 300, "skill-01", ".", "..",
]


def name_skills(monkeypatch, tmp_path, names):
    """A skill folder for each name, which Skill.load gives that name."""
    paths = [make_skill(tmp_path, f"folder{i}") for i in range(len(names))]
    given = dict(zip(map(str, paths), names))
    real_load = evaluate.Skill.load

    def load(path):
        skill = real_load(path)
        skill.name = given[str(path)]
        return skill
    monkeypatch.setattr(evaluate.Skill, "load", staticmethod(load))
    return paths


def test_no_skill_name_breaks_the_report_or_shares_a_log_folder(tmp_path, harness, monkeypatch):
    paths = name_skills(monkeypatch, tmp_path, HOSTILE_NAMES)
    harness.agent_act = lambda prompt, skill, workdir: AgentRun(
        output=f"from {Path(skill.path).name if skill else 'baseline'}", input_tokens=1,
        llm_calls=1)
    result = rank_skills(paths, config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    run_dir = Path(result["artifacts"][0]).parent
    folders = [evaluate._log_folder(n) for n in range(1, len(paths) + 1)]
    # Only the report's two files and the numbered folders, and nothing outside.
    assert sorted(p.name for p in run_dir.iterdir()) \
        == sorted([*folders, "ranked-evaluation.json", "ranked-evaluation.md"])
    assert [p.name for p in (tmp_path / "out").iterdir()] == [run_dir.name]
    skills = report(result).skills
    assert len(skills) == len(paths) and result["task_runs"]["errors"] == 0
    assert sorted(s.log_folder for s in skills) == sorted(folders)
    given = dict(zip(folders, paths))
    for skill in skills:  # each skill's runs, and only they, are in its folder
        log = (run_dir / skill.log_folder / "logs" / "t1-with_skill-r1.txt").read_text("utf-8")
        assert f"from {given[skill.log_folder].name}" in log
        assert all(f"(full log: {skill.log_folder}/logs/" in t.output for t in skill.tests)


def test_without_tasks_no_log_folder_is_named(tmp_path, harness):
    result = rank_skills([make_skill(tmp_path / "x", "same"), make_skill(tmp_path / "y", "same")],
                         output_path=tmp_path / "out")
    assert [s.log_folder for s in report(result).skills] == [None, None]
    assert not any("Logs" in w for w in result["warnings"])


def test_a_skill_whose_logs_cannot_be_written_keeps_its_row(tmp_path, harness, monkeypatch):
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.parts[-2:] == ("skill-01", "logs"):
            raise PermissionError("no")
        return real_mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    first, second = sorted(report(result).skills, key=lambda s: len(s.tests))
    assert first.tests == [] and second.tests
    # No folder is named for runs that were never made.
    assert (first.log_folder, second.log_folder) == (None, "skill-02")
    assert first.dimensions[Dimension.EFFICIENCY].status is Status.ERROR
    assert first.dimensions[Dimension.SAFETY].status is not Status.SKIPPED
    assert result["task_runs"]["errors"] == 1
    assert any("PermissionError" in w for w in result["warnings"])


# --- Config ------------------------------------------------------------------------------------


def test_task_settings_round_trip_and_where_the_file_is_does_not_change_the_hash():
    config = EvaluationConfig(tasks_path=Path("evals/t.json"), agent_model="m", agent_provider="p")
    again = EvaluationConfig.from_dict(json.loads(json.dumps(config.to_dict())))
    assert (again.tasks_path, again.agent_model, again.agent_provider) == (Path("evals/t.json"), "m", "p")
    moved = EvaluationConfig(tasks_path=Path("/elsewhere/t.json"), agent_model="m", agent_provider="p")
    assert config.hash() == moved.hash()
    assert config.hash() != EvaluationConfig(tasks_path=Path("evals/t.json"), agent_model="other").hash()
    assert EvaluationConfig.from_dict({}).tasks_path is None


# --- Before the runs: the agent is checked once ----------------------------------------------


class CheckedAgent(FakeAgent):
    """A FakeAgent with HermesAgent's model-free check, which raises `fails` if given."""

    def __init__(self, act, fails=None):
        super().__init__(act)
        self.models_used, self.fails, self.checks = set(), fails, 0

    def check(self, workspace):
        self.checks += 1
        if self.fails:
            raise self.fails
        return {"status": "ok"}


def use(harness, monkeypatch, agent):
    monkeypatch.setattr(evaluate, "_make_agent", lambda config: harness.agents.append(agent) or agent)


def test_an_agent_that_cannot_be_set_up_is_one_error_and_nothing_runs(tmp_path, harness, monkeypatch):
    agent = CheckedAgent(cheap_with_skill, fails=HermesAgentUnavailable(
        "the Hermes check exited 1 without a result", stderr='File "/home/someone/.hermes/x.py"'))
    use(harness, monkeypatch, agent)
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    assert result["status"] == "error" and "could not be set up, so nothing was run" in result["error"]
    assert "exited 1 without a result" in result["error"]
    assert "/home/someone" not in json.dumps(result)  # stderr stays out of the response
    assert agent.checks == 1 and agent.calls == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("failure", [TimeoutError("hung"), RunFailed("docker died")])
def test_an_agent_check_that_hangs_or_loses_docker_is_an_error_too(tmp_path, harness, monkeypatch, failure):
    agent = CheckedAgent(cheap_with_skill, fails=failure)
    use(harness, monkeypatch, agent)
    result = evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert result["status"] == "error" and type(failure).__name__ in result["error"]
    assert agent.calls == []


def test_a_working_agent_is_checked_once_per_call_then_runs(tmp_path, harness, monkeypatch):
    agent = CheckedAgent(cheap_with_skill)
    use(harness, monkeypatch, agent)
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    assert agent.checks == 1 and result["task_runs"]["sessions"] == 2 * 2 * RUNS * 2
    assert harness.sandbox.opened[0].closed  # the check's workspace is closed


# --- The judge of the task runs' LLM checks is recorded ---------------------------------------


def test_the_judge_of_llm_checks_is_recorded_in_the_report(tmp_path, harness, monkeypatch):
    harness.tasks.write_text(json.dumps({"tasks": [{
        "task_id": "report", "description": "d", "prompt": "write the report",
        "checks": [{"judge": "llm", "checklist": ["says it wrote the report"]}]}]}))
    reply = {"items": [{"item": 1, "met": True, "reason": "", "quote": "wrote the report to report.txt"}]}
    harness.agent_act = writes_report_with_skill
    ctx = SimpleNamespace(llm=FakeLlm(*[reply] * 8))
    harness.config.write_text(json.dumps({"runs_per_task": 1, "judge_retries": 0}))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks, ctx=ctx)
    meta = report(result).meta
    assert meta.judge_model == "fake/judge-model"
    # Two task-check calls (with the skill, baseline) plus Clarity's one, which the run's
    # shared judge makes in the same evaluate; its reply does not fit the rubric, but the
    # call was made and used tokens.
    assert meta.judge_usage == {"input_tokens": 150, "output_tokens": 15, "calls": 3}


def test_without_llm_checks_no_judge_is_recorded(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert report(result).meta.judge_model == "" and report(result).meta.judge_usage == {}


# --- Every judge reaches the report header (MET-51) ---------------------------------------


OWN_JUDGE = (  # a dimension that builds a judge of its own and reports it on its score
    "from metaskill.evaluate.models import DimensionScore, Dimension\n"
    "def score(skill, config, llm=None, **_):\n"
    "    return DimensionScore(Dimension.CONFIG_CLEANLINESS, 0.8, judge_model='openai/rubric-model',\n"
    "                          judge_usage={'input_tokens': 500, 'output_tokens': 50, 'calls': 1})\n")


def test_every_judge_is_counted_once_in_the_header(tmp_path, harness, placeholder):
    # Three sources, each once: the task runs' LLM checks (2 calls), the run's shared judge
    # that Clarity uses (1 call; its reply does not fit the rubric, but the call was made),
    # and a dimension with a judge of its own, reported on its score (1 call).
    placeholder(Dimension.CONFIG_CLEANLINESS, OWN_JUDGE)
    harness.tasks.write_text(json.dumps({"tasks": [{
        "task_id": "report", "description": "d", "prompt": "write the report",
        "checks": [{"judge": "llm", "checklist": ["says it wrote the report"]}]}]}))
    reply = {"items": [{"item": 1, "met": True, "reason": "", "quote": "wrote the report to report.txt"}]}
    harness.agent_act = writes_report_with_skill
    harness.config.write_text(json.dumps({"runs_per_task": 1, "judge_retries": 0}))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks,
                            ctx=SimpleNamespace(llm=FakeLlm(*[reply] * 8)))
    meta = report(result).meta
    assert meta.judge_model == "fake/judge-model, openai/rubric-model"
    assert meta.judge_usage == {"input_tokens": 650, "output_tokens": 65, "calls": 4}


def test_a_dimensions_own_judge_is_recorded_without_tasks(tmp_path, placeholder):
    placeholder(Dimension.CONFIG_CLEANLINESS, OWN_JUDGE)
    meta = report(evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out")).meta
    assert meta.judge_model == "openai/rubric-model"
    assert meta.judge_usage == {"input_tokens": 500, "output_tokens": 50, "calls": 1}


# --- Task dimensions get the planned task ids (for Quality's assess) ---------------------


def test_a_task_dimension_receives_the_planned_task_ids(tmp_path, harness, placeholder):
    # A stand-in task-based dimension (robustness), as Quality's score_runs will use them.
    placeholder(Dimension.ROBUSTNESS, (
        "from metaskill.evaluate.models import DimensionScore, Dimension, Status\n"
        "def score(skill, config, llm=None):\n"
        "    raise AssertionError('task dimensions are scored from runs')\n"
        "def score_runs(skill, config, runs):\n"
        "    return DimensionScore(Dimension.ROBUSTNESS, 0.5, Status.OK,\n"
        "                          detail=','.join(runs.task_ids) + f'|{len(runs.tests)}')\n"))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    assert report(result).skills[0].dimensions[Dimension.ROBUSTNESS].detail == f"t1,t2|{2 * RUNS * 2}"



# --- Quality scores the runs, and its lift reaches the report -----------------------------


def test_a_skill_that_changes_the_outcome_gets_its_lift_and_quality(tmp_path, harness):
    # Only the skill makes the agent write report.txt, so with-skill runs pass and
    # baseline runs fail: lift +1, and the provisional formula gives Quality 1.0.
    harness.tasks.write_text(json.dumps({"tasks": [{
        "task_id": "report", "description": "d", "prompt": "write the report",
        "checks": [{"judge": "deterministic", "kind": "file_exists", "target": "report.txt"}]}]}))
    harness.agent_act = writes_report_with_skill
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    skill = report(result).skills[0]
    assert skill.lift == 1.0
    assert skill.dimensions[Dimension.QUALITY].status is Status.OK and skill.scores.quality == 1.0
    assert "1 task(s), 2 run(s) per task per condition" in skill.dimensions[Dimension.QUALITY].detail



# --- Robustness scores the runs, and the ranking uses it ------------------------------------


def flaky_for(name):
    """The agent with skill `name` passes every run, but answers differently each time;
    with any other skill, or none, it answers the same way every time. Pass rates, costs
    and lift are equal, so only Robustness tells the two skills apart."""
    seen = []

    def act(prompt, skill, workdir):
        tokens = dict(input_tokens=100, output_tokens=10, llm_calls=2, tool_calls=1)
        if skill is not None and skill.name == name:
            seen.append(prompt)
            return AgentRun(output=f"answer {len(seen)}\nwritten another way {len(seen)}", **tokens)
        return AgentRun(output="the answer", **tokens)
    return act


def test_rank_orders_a_consistent_skill_above_a_flaky_one_on_robustness(tmp_path, harness,
                                                                        placeholder):
    # Clarity needs a model and Config Cleanliness is not complete yet; fixed stand-ins let
    # both skills be ranked, so robustness decides the order.
    for dimension in (Dimension.CLARITY, Dimension.CONFIG_CLEANLINESS):
        placeholder(dimension, (
            "from metaskill.evaluate.models import DimensionScore, Dimension\n"
            "def score(skill, config, llm=None, **_):\n"
            f"    return DimensionScore(Dimension.{dimension.name}, 0.5)\n"))
    harness.agent_act = flaky_for("flaky")
    result = rank_skills([make_skill(tmp_path, "flaky"), make_skill(tmp_path, "steady")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    assert result["counts"]["ranked"] == 2
    steady, flaky = report(result).skills  # in ranking order
    assert (steady.name, flaky.name) == ("steady", "flaky")
    assert steady.scores.robustness == 1.0 and flaky.scores.robustness < 1.0
    for dimension in Dimension:  # robustness is the only difference
        if dimension is not Dimension.ROBUSTNESS:
            assert getattr(steady.scores, dimension.value) == getattr(flaky.scores, dimension.value)
    assert steady.scores.overall > flaky.scores.overall
    assert "Use steady" in result["table"]
    rows = [line for line in result["table"].splitlines() if line.startswith("| 1 ")
            or line.startswith("| 2 ")]
    assert rows[0].startswith("| 1 | steady | ") and " 1.00 " in rows[0]


@pytest.mark.parametrize("dimension", sorted(evaluate.TASK_DIMENSIONS, key=lambda d: d.value),
                         ids=lambda d: d.value)
def test_the_placeholder_replaces_built_task_dimensions_too(tmp_path, harness, placeholder, dimension):
    # Quality and Efficiency are built; a real module would shadow a stand-in of the same
    # name. The placeholder's own module name does not, and it gets the skill's runs.
    placeholder(dimension, (
        "from metaskill.evaluate.models import DimensionScore, Dimension\n"
        f"def score_runs(skill, config, runs):\n"
        f"    return DimensionScore(Dimension.{dimension.name}, 0.5, detail=str(len(runs.tests)))\n"))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    skill = report(result).skills[0]
    assert getattr(skill.scores, dimension.value) == 0.5
    assert skill.dimensions[dimension].detail == str(2 * RUNS * 2)


def test_task_dimensions_are_skipped_without_a_task_file_even_when_built(tmp_path, placeholder):
    # score() is for a call without tasks; a task dimension is skipped there, not scored.
    placeholder(Dimension.QUALITY, (
        "def score(skill, config, llm=None, **_):\n"
        "    raise AssertionError('a task dimension is not scored without tasks')\n"))
    status = report(evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out")
                    ).skills[0].dimensions[Dimension.QUALITY]
    assert status.status is Status.SKIPPED and "no tasks were given" in status.detail


# --- The task file is not part of a skill ------------------------------------------------


def test_a_task_file_inside_the_skill_is_refused_before_any_run(tmp_path, harness):
    skill = make_skill(tmp_path, "s")
    inside = skill / "tasks.json"
    inside.write_text(harness.tasks.read_text())
    (skill / "data.csv").write_text("a,b\n1,2\n")
    result = evaluate_skill(skill, output_path=tmp_path / "out", tasks_path=inside)
    assert result["status"] == "error" and "outside every evaluated skill" in result["error"]
    assert harness.agents == [] and harness.sandbox.opened == []


def test_a_task_file_inside_any_ranked_skill_is_refused(tmp_path, harness):
    clean_skill, other = make_skill(tmp_path, "a"), make_skill(tmp_path, "b")
    inside = other / "evals" / "tasks.json"
    inside.parent.mkdir()
    inside.write_text(harness.tasks.read_text())
    (inside.parent / "data.csv").write_text("a,b\n1,2\n")
    result = rank_skills([clean_skill, other / "SKILL.md"], output_path=tmp_path / "out",
                         tasks_path=inside)
    assert result["status"] == "error" and "outside every evaluated skill" in result["error"]
    assert harness.agents == [] and harness.sandbox.opened == []


def test_a_task_file_beside_the_skill_is_fine(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out",
                            tasks_path=harness.tasks)
    assert "artifacts" in result


# --- The agent's limits (MET-49) ---------------------------------------------------------------


def test_the_agent_limits_default_to_what_was_hard_coded():
    config = EvaluationConfig()
    assert (config.agent_max_iterations, config.agent_timeout) == (30, 600.0)
    agent = evaluate._make_agent(config)
    assert (agent.max_iterations, agent.timeout) == (30, 600.0)


def test_the_agent_limits_round_trip_reach_the_agent_and_change_the_hash():
    config = EvaluationConfig(agent_max_iterations=12, agent_timeout=90.5)
    again = EvaluationConfig.from_dict(json.loads(json.dumps(config.to_dict())))
    assert (again.agent_max_iterations, again.agent_timeout) == (12, 90.5)
    assert again.hash() == config.hash()
    agent = evaluate._make_agent(config)
    assert (agent.max_iterations, agent.timeout) == (12, 90.5)
    # Each changes what an agent can finish, so each changes the hash.
    assert config.hash() != EvaluationConfig(agent_max_iterations=13, agent_timeout=90.5).hash()
    assert config.hash() != EvaluationConfig(agent_max_iterations=12, agent_timeout=91).hash()
    assert EvaluationConfig.from_dict({}).agent_max_iterations == 30


def test_a_whole_number_of_seconds_is_the_same_limit_as_its_float():
    assert EvaluationConfig(agent_timeout=600).hash() == EvaluationConfig().hash()
    assert EvaluationConfig.from_dict({"agent_timeout": 90}).to_dict()["agent_timeout"] == 90.0


@pytest.mark.parametrize("field, value", [
    ("agent_max_iterations", 0), ("agent_max_iterations", -3), ("agent_max_iterations", True),
    ("agent_max_iterations", 2.5), ("agent_max_iterations", "30"), ("agent_max_iterations", None),
    ("agent_timeout", 0), ("agent_timeout", -1), ("agent_timeout", float("nan")),
    ("agent_timeout", float("inf")), ("agent_timeout", True), ("agent_timeout", "600"),
    ("agent_timeout", None),
])
def test_invalid_agent_limits_are_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        EvaluationConfig(**{field: value})
    with pytest.raises(ValueError, match=field):
        EvaluationConfig.from_dict({field: value})


def test_an_invalid_limit_in_the_config_file_is_an_error_before_anything_runs(tmp_path, harness):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"agent_timeout": 0}))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=bad, output_path=tmp_path / "out",
                            tasks_path=harness.tasks)
    assert result["status"] == "error" and "not a valid evaluation config" in result["error"]
    assert harness.agents == []


# --- Dimensions that do not read the runs are scored while they run ---------------------


def test_dimensions_without_runs_are_scored_on_a_background_thread_in_order(tmp_path, harness,
                                                                            monkeypatch):
    import threading
    real, threads, order = evaluate._static_scores, set(), []

    def recording(skill, *args):
        threads.add(threading.current_thread().name)
        order.append(skill.name)
        return real(skill, *args)

    monkeypatch.setattr(evaluate, "_static_scores", recording)
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    assert order == ["a", "b"] and all(t.startswith("metaskill-static") for t in threads)
    skills = report(result).skills
    assert {s.name for s in skills} == {"a", "b"}
    for skill in skills:  # every dimension is there, the static ones too
        assert skill.dimensions[Dimension.SAFETY].status is not Status.SKIPPED
        assert skill.dimensions[Dimension.EFFICIENCY].status is not Status.SKIPPED


def test_without_tasks_the_dimensions_are_scored_in_the_calling_thread(tmp_path, harness,
                                                                       monkeypatch):
    import threading
    real, threads = evaluate._static_scores, set()

    def recording(*args):
        threads.add(threading.current_thread().name)
        return real(*args)

    monkeypatch.setattr(evaluate, "_static_scores", recording)
    evaluate_skill(make_skill(tmp_path, "s"), output_path=tmp_path / "out")
    assert threads == {threading.current_thread().name}


def test_the_background_thread_keeps_the_callers_context(tmp_path, harness, monkeypatch):
    # Hermes scopes a profile's credentials in a contextvar: Clarity's judge calls on the
    # background thread failed with "could not read this profile's ANTHROPIC_TOKEN".
    import contextvars
    scope = contextvars.ContextVar("scope", default=None)
    real, seen = evaluate._static_scores, []

    def recording(*args):
        seen.append(scope.get())
        return real(*args)

    monkeypatch.setattr(evaluate, "_static_scores", recording)
    token = scope.set("profile")
    try:
        rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                    config_path=harness.config, output_path=tmp_path / "out",
                    tasks_path=harness.tasks)
    finally:
        scope.reset(token)
    assert seen == ["profile", "profile"]


# --- The dimensions scored in the background share the runs' rate-limit pause -------------


class RateLimitError(Exception):
    """Named like the provider SDKs' error."""


def test_a_rate_limit_on_a_background_judge_call_is_waited_out_not_an_error(
        tmp_path, harness, monkeypatch):
    # Review of #53: Clarity's one judge call, made while the sessions ran, got a 429 and
    # was not retried, so the skill had no Clarity score, no overall and no rank.
    import functools
    from metaskill.evaluate.backoff import Backoff
    monkeypatch.setattr(evaluate, "Backoff",
                        functools.partial(Backoff, sleep=lambda s: None, jitter=0))

    class LimitedOnce(FakeLlm):
        calls = 0

        def complete_structured(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError("429 Too Many Requests")
            return super().complete_structured(**kwargs)

    llm = LimitedOnce(*[{"ratings": []}] * 8)
    harness.config.write_text(json.dumps({"runs_per_task": RUNS, "judge_retries": 0,
                                          "max_parallel_runs": 4}))
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks,
                            ctx=SimpleNamespace(llm=llm))
    clarity = report(result).skills[0].dimensions[Dimension.CLARITY]
    assert llm.calls == 2  # the limited call, and the same call made again
    assert clarity.detail != evaluate.PROVIDER_FAILED


def test_a_failed_skills_background_judge_use_is_still_counted(tmp_path, harness, placeholder,
                                                                 monkeypatch):
    # Review of #53: a skill whose result failed after its background scoring started left
    # that scoring's judge usage out of the report header.
    placeholder(Dimension.CONFIG_CLEANLINESS, OWN_JUDGE)
    real = evaluate.run_tasks

    def failing_for_a(skill, *args, **kwargs):
        if skill.name == "a":
            raise RuntimeError("the runner broke")
        return real(skill, *args, **kwargs)

    monkeypatch.setattr(evaluate, "run_tasks", failing_for_a)
    result = rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                         config_path=harness.config, output_path=tmp_path / "out",
                         tasks_path=harness.tasks)
    assert any("could not be evaluated" in w for w in result["warnings"])
    meta = report(result).meta
    assert meta.judge_model == "openai/rubric-model"
    assert meta.judge_usage == {"input_tokens": 1000, "output_tokens": 100, "calls": 2}


def test_an_interrupt_stops_the_background_scoring_at_its_next_dimension(tmp_path, harness,
                                                                         monkeypatch):
    # Ctrl-C left the background thread scoring the skill's remaining dimensions, with
    # their model calls, after the call had ended.
    import threading
    started, release, done, scored = threading.Event(), threading.Event(), threading.Event(), []
    real_score, real_static = evaluate._score, evaluate._static_scores

    def slow_first(dimension, *args):
        scored.append(dimension)
        if len(scored) == 1:
            started.set()
            release.wait(10)
        return real_score(dimension, *args)

    def static(*args):
        try:
            return real_static(*args)
        finally:
            done.set()

    def interrupted(*args, **kwargs):
        started.wait(10)  # the background scoring is under way
        raise KeyboardInterrupt

    monkeypatch.setattr(evaluate, "_score", slow_first)
    monkeypatch.setattr(evaluate, "_static_scores", static)
    monkeypatch.setattr(evaluate, "run_tasks", interrupted)
    with pytest.raises(KeyboardInterrupt):
        evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                       output_path=tmp_path / "out", tasks_path=harness.tasks)
    release.set()
    assert done.wait(10)
    assert len(scored) == 1  # the dimension under way ended; no later one started


def test_an_agent_a_stopped_call_cancelled_is_resumed_before_its_check(tmp_path, harness,
                                                                       monkeypatch):
    class Cancelled(CheckedAgent):
        cancelled = True

        def resume(self):
            self.cancelled = False

        def check(self, workspace):
            if self.cancelled:  # as HermesAgent: a cancelled agent kills the check's process
                raise HermesAgentUnavailable("the check was stopped")
            return super().check(workspace)

    agent = Cancelled(cheap_with_skill)
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks, agent=agent)
    assert result.get("status") != "error" and agent.checks == 1
