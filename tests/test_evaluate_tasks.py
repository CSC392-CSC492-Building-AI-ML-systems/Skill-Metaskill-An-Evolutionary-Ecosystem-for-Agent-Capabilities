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


def test_run_logs_go_into_this_calls_report_folder(tmp_path, harness):
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks)
    run_dir = Path(result["artifacts"][0]).parent
    logs = sorted(p.name for p in (run_dir / "s" / "logs").iterdir())
    assert len(logs) == 2 * RUNS * 2 and "t1-with_skill-r1.txt" in logs


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
    # Every with-skill run failed, so efficiency has nothing to credit (success-gated).
    assert skill.dimensions[Dimension.EFFICIENCY].status is Status.SKIPPED


def test_a_skill_name_that_cannot_be_a_log_folder_still_gets_safety(tmp_path, harness, monkeypatch):
    # The name comes from the skill's frontmatter; set a hostile one directly, since
    # frontmatter needs Hermes' YAML loader.
    hostile = evaluate.Skill(path=make_skill(tmp_path, "s"), name="../escape", body="# Skill\n")
    monkeypatch.setattr(evaluate.Skill, "load", staticmethod(lambda path: hostile))
    result = evaluate_skill(hostile.path, config_path=harness.config, output_path=tmp_path / "out",
                            tasks_path=harness.tasks)
    assert result["task_runs"]["errors"] == 1 and result["task_runs"]["sessions"] == 0
    skill = report(result).skills[0]
    assert skill.dimensions[Dimension.EFFICIENCY].status is Status.ERROR
    assert skill.dimensions[Dimension.SAFETY].status is not Status.SKIPPED
    assert harness.agents[0].calls == [] and not (tmp_path / "escape").exists()


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
