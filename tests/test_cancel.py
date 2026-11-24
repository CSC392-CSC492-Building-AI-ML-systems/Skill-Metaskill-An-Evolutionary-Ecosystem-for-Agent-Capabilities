"""Stopping a running evaluation: /metaskill stop and Hermes' interrupt flag (cancel.py).
Ctrl-C does not reach an evaluation Hermes runs in its gateway, so these are how it stops."""

import threading
import time
from pathlib import Path

import pytest

from metaskill.evaluate import cancel, runner
from metaskill.evaluate.models import EvaluationConfig
from metaskill.evaluate.runner import AgentRun, run_tasks
from sandbox_helpers import FakeSandbox
from test_runner import FakeAgent, task


@pytest.fixture
def skill(tmp_path):
    from metaskill.evaluate.models import Skill
    path = tmp_path / "cleanup"
    path.mkdir()
    (path / "SKILL.md").write_text("# Cleanup\n", encoding="utf-8")
    return Skill(path=path, name="cleanup", body="# Cleanup\n")


@pytest.fixture(autouse=True)
def quick_poll(monkeypatch):
    monkeypatch.setattr(cancel, "POLL", 0.05)
    monkeypatch.setattr(cancel.Watcher.__init__, "__defaults__", (0.05,))


class BlockingAgent(FakeAgent):
    """A session that runs until it is cancelled, as a Hermes child does until killed."""

    def __init__(self):
        self.started = threading.Event()
        self.stop = threading.Event()
        self.cancelled = 0
        super().__init__(self.act)

    def act(self, prompt, skill, workdir):
        self.started.set()
        if not self.stop.wait(30):
            raise AssertionError("the session was never stopped")
        raise RuntimeError("killed")  # what a killed Hermes child looks like to the runner

    def cancel(self):
        self.cancelled += 1
        self.stop.set()


# --- The registry ---------------------------------------------------------------------


def test_a_stop_is_seen_by_check_and_the_run_unregisters(monkeypatch):
    with cancel.running() as run:
        cancel.check()  # not stopped yet
        assert cancel.stop_all() == 1
        with pytest.raises(cancel.EvaluationStopped, match="/metaskill stop"):
            cancel.check()
        assert run.requested()
    assert cancel.current() is None and cancel.stop_all() == 0


def test_stop_reaches_only_the_same_profile(monkeypatch):
    owner = ["profile-a"]
    monkeypatch.setattr(cancel, "_owner", lambda: owner[0])
    with cancel.running() as mine:
        owner[0] = "profile-b"
        assert cancel.stop_all() == 0  # another profile cannot stop it
        assert not mine.requested()
        owner[0] = "profile-a"
        assert cancel.stop_all() == 1 and mine.requested()


def test_hermes_interrupting_the_turn_stops_it_but_a_stale_flag_does_not(monkeypatch):
    flag = [True]
    monkeypatch.setattr(cancel, "_hermes_interrupted", lambda tid: flag[0])
    with cancel.running() as stale:  # set before the evaluation started: an earlier turn's
        assert not stale.requested()
    flag[0] = False
    with cancel.running() as run:
        assert not run.requested()
        flag[0] = True
        assert run.requested() and "interrupted" in run.reason


def test_outside_hermes_the_interrupt_flag_is_never_set():
    assert cancel._hermes_interrupted(threading.get_ident()) is False


# --- The runner stops -------------------------------------------------------------------


@pytest.mark.parametrize("parallel", [1, 4])
def test_a_stop_ends_the_runs_and_removes_every_container(skill, tmp_path, parallel):
    agent, sandbox = BlockingAgent(), FakeSandbox()
    stopper = threading.Thread(target=lambda: (agent.started.wait(10), cancel.stop_all()))
    with cancel.running():
        stopper.start()
        start = time.monotonic()
        with pytest.raises(cancel.EvaluationStopped):
            run_tasks(skill, [task("a"), task("b")], agent,
                      config=EvaluationConfig(runs_per_task=2, max_parallel_runs=parallel),
                      run_dir=tmp_path, sandbox=sandbox)
    stopper.join()
    assert time.monotonic() - start < 10  # not the 30 s a session would block
    assert agent.cancelled >= 1  # the agent's Hermes sessions were stopped
    assert sandbox.opened and all(w.closed for w in sandbox.opened)
    assert len(sandbox.opened) < 8  # not every planned session started
    assert not [t for t in threading.enumerate()
                if t.name.startswith(("metaskill-run", "metaskill-stop-watch"))]


def test_a_run_outside_an_evaluation_has_no_watcher(skill, tmp_path):
    result = run_tasks(skill, [task("a")], FakeAgent(lambda p, s, w: AgentRun(output="x")),
                       config=EvaluationConfig(runs_per_task=1), run_dir=tmp_path,
                       sandbox=FakeSandbox())
    assert len(result.tests) + len(result.errors) == 2
    assert not [t for t in threading.enumerate() if t.name == "metaskill-stop-watch"]


# --- evaluate and the command -----------------------------------------------------------


def test_a_stopped_rank_says_so_writes_no_report_and_closes_its_agent(tmp_path, monkeypatch):
    import json

    import metaskill.evaluate as evaluate
    from test_evaluate_tasks import ReadySandbox, make_skill

    agent = BlockingAgent()
    closed = []
    agent.close = lambda: closed.append(True)
    monkeypatch.setattr(evaluate, "_make_agent", lambda config: agent)
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: ReadySandbox())
    tasks = tmp_path / "evals" / "tasks.json"
    tasks.parent.mkdir()
    (tasks.parent / "data.csv").write_text("a\n")
    tasks.write_text(json.dumps({"tasks": [
        {"task_id": "t1", "description": "d", "prompt": "p", "fixtures": ["data.csv"],
         "checks": [{"judge": "deterministic", "kind": "file_exists", "target": "x"}]}]}))
    threading.Thread(target=lambda: (agent.started.wait(10), cancel.stop_all()),
                     daemon=True).start()
    result = evaluate.rank_skills([make_skill(tmp_path, "a"), make_skill(tmp_path, "b")],
                                  output_path=tmp_path / "out", tasks_path=tasks)
    assert result["status"] == "error" and result["stopped"] is True
    assert "stopped" in result["error"] and "/metaskill stop" in result["error"]
    assert not list((tmp_path / "out").rglob("ranked-evaluation.json"))
    assert closed == [True]
    # Skill b never ran: the stop ended the whole call, not skill a alone.
    assert {c.skill for c in agent.calls} <= {"a", None}


def test_the_stop_command_reports_what_it_stopped():
    import tools

    handle = tools.make_command_handler(None)
    assert handle("stop") == "No metaskill evaluation is running."
    with cancel.running() as run:
        out = handle("stop")
        assert out.startswith("Stopping 1 running evaluation(s)") and run.requested()
    assert handle("stop now").startswith("Usage: /metaskill stop")
