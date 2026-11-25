"""Warm start: Hermes children started ahead of their sessions (HermesAgent.prewarm,
hermes_child --warm). Real child processes with a fake child script, so the hand-off on
stdin, the stderr file and the kills are the real ones; no Hermes, model or Docker."""

import io
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from metaskill.evaluate import hermes_agent, hermes_child
from metaskill.evaluate.hermes_agent import HermesAgent, HermesAgentUnavailable
from metaskill.evaluate.models import EvaluationConfig
from test_hermes_agent import OK_RESULT, alive, docker_workspace

# A fake child that behaves like hermes_child: with --warm it writes its pid, then waits for
# the request path on stdin (exiting on EOF); otherwise the path is argv[1].
CHILD = """\
import json, os, sys, time
if sys.argv[1] == "--warm":
    open(os.environ["WARM_PIDS"], "a").write(f"{os.getpid()}\\n")
    path = sys.stdin.readline().strip()
    if not path:
        sys.exit(0)
else:
    path = sys.argv[1]
request = json.load(open(path))
sys.stderr.write("child stderr line\\n")
MODE = os.environ.get("CHILD_MODE", "ok")
if MODE == "hang":
    time.sleep(60)
if MODE != "no-result":
    json.dump(dict(RESULT, model="warm" if "--warm" in sys.argv else "cold"), open(request["result"], "w"))
"""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    child = tmp_path / "child.py"
    child.write_text(f"RESULT = {OK_RESULT!r}\n" + CHILD, encoding="utf-8")
    pids = tmp_path / "warm-pids.txt"
    monkeypatch.setenv("WARM_PIDS", str(pids))
    made = HermesAgent(stagger=0.02)  # the stagger's own tests set theirs
    made._argv = [sys.executable, str(child)]
    made.pids = pids
    yield made
    made.cancel()  # no child outlives a test


def warm_pids(agent):
    return [int(p) for p in agent.pids.read_text().split()] if agent.pids.exists() else []


def wait_for(condition, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


# --- The child's side ------------------------------------------------------------------


def test_the_child_waits_for_its_request_line():
    assert hermes_child.wait_for_request(io.StringIO("/tmp/request.json\n")) == "/tmp/request.json"


def test_the_child_gives_up_on_eof_or_when_idle_too_long():
    assert hermes_child.wait_for_request(io.StringIO("")) is None  # the parent closed stdin

    class Silent:
        """A stdin nothing is written to. Not a real pipe: closing one while a thread is
        blocked reading it waits for that read forever."""
        released = threading.Event()

        def readline(self):
            self.released.wait(30)
            return ""

    never = Silent()
    start = time.monotonic()
    try:
        assert hermes_child.wait_for_request(never, idle=0.2) is None
        assert time.monotonic() - start < 5
    finally:
        never.released.set()


def test_preload_skips_what_will_not_import(monkeypatch):
    monkeypatch.setattr(hermes_child, "_PRELOAD", ("json", "no_such_module_anywhere"))
    hermes_child.preload()  # no raise: the session reports it, as a cold one would


# --- The pool ------------------------------------------------------------------------


def test_a_session_uses_a_warm_child_and_a_replacement_starts(agent):
    agent.prewarm(2)
    assert wait_for(lambda: len(warm_pids(agent)) == 2)
    run = agent.run("p", None, docker_workspace())
    assert run.output == "60" and agent.models_used == {"warm"}  # served by a warm child
    assert wait_for(lambda: len(warm_pids(agent)) == 3)  # its replacement
    assert len(agent._idle) == 2


def test_without_prewarm_or_with_warm_off_sessions_start_cold(agent):
    agent.run("p", None, docker_workspace())
    assert agent.models_used == {"cold"} and warm_pids(agent) == []
    agent.warm = False
    agent.prewarm(4)
    assert agent._idle == [] and warm_pids(agent) == []


def test_a_warm_session_keeps_its_timeout(agent, monkeypatch):
    agent.timeout = 1.0
    monkeypatch.setenv("CHILD_MODE", "hang")  # the child hangs once it has its request
    agent.prewarm(1)
    assert wait_for(lambda: len(warm_pids(agent)) == 1)
    time.sleep(1.5)  # waiting longer than the timeout before the session must not count
    pid = warm_pids(agent)[0]
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        agent.run("p", None, docker_workspace())
    assert time.monotonic() - start < 10  # counted from the hand-off, and the child stopped
    assert wait_for(lambda: not alive(pid))


def test_a_warm_child_without_a_result_reports_its_stderr(agent, monkeypatch):
    monkeypatch.setenv("CHILD_MODE", "no-result")
    agent.prewarm(1)
    assert wait_for(lambda: len(warm_pids(agent)) == 1)
    with pytest.raises(HermesAgentUnavailable, match="child stderr line"):
        agent.run("p", None, docker_workspace())


def test_a_dead_warm_child_is_skipped_and_the_session_starts_cold(agent):
    agent.prewarm(1)
    assert wait_for(lambda: len(warm_pids(agent)) == 1)
    agent._idle[0].proc.stdin.close()  # it exits on EOF, as after its idle limit
    assert wait_for(lambda: agent._idle[0].proc.poll() is not None)
    agent.close()  # no replacement for this test: the session must start cold
    agent._target = 0
    agent.run("p", None, docker_workspace())
    assert agent.models_used == {"cold"}


def test_close_and_cancel_stop_every_waiting_child(agent):
    agent.prewarm(3)
    assert wait_for(lambda: len(warm_pids(agent)) == 3)
    pids = warm_pids(agent)
    agent.close()
    assert agent._idle == [] and wait_for(lambda: not any(alive(p) for p in pids))
    agent.prewarm(2)
    assert wait_for(lambda: len(warm_pids(agent)) == 5)
    agent.cancel()
    assert agent._idle == [] and wait_for(lambda: not any(alive(p) for p in warm_pids(agent)))
    agent.prewarm(2)  # cancelled: no new children
    assert agent._idle == [] and len(warm_pids(agent)) == 5


def test_a_child_that_cannot_start_leaves_sessions_cold(agent, monkeypatch):
    def refuse(argv):
        raise OSError("Windows refused")
    monkeypatch.setattr(hermes_agent._Warm, "start", staticmethod(refuse))
    agent.prewarm(3)  # no raise
    assert agent._idle == []
    agent.run("p", None, docker_workspace())
    assert agent.models_used == {"cold"}


def test_parallel_takes_never_share_a_child(agent):
    agent.prewarm(4)
    assert wait_for(lambda: len(warm_pids(agent)) == 4)
    taken, lock = [], threading.Lock()

    def take():
        warm = agent._take()
        with lock:
            taken.append(warm)

    threads = [threading.Thread(target=take) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert None not in taken and len({id(w) for w in taken}) == 4
    for warm in taken:
        warm.discard()


# --- Wiring -------------------------------------------------------------------------


def test_prewarm_agents_is_a_setting_left_out_of_the_hash():
    assert EvaluationConfig().prewarm_agents is True
    off = EvaluationConfig.from_dict({"prewarm_agents": False})
    assert off.prewarm_agents is False and off.to_dict()["prewarm_agents"] is False
    assert off.hash() == EvaluationConfig().hash()
    with pytest.raises(ValueError, match="prewarm_agents"):
        EvaluationConfig(prewarm_agents="yes")


def test_evaluate_makes_its_agent_with_the_setting():
    from metaskill import evaluate
    assert evaluate._make_agent(EvaluationConfig(prewarm_agents=False)).warm is False


def test_the_runner_prewarms_one_per_worker(tmp_path):
    from metaskill.evaluate.models import Skill
    from metaskill.evaluate.runner import run_tasks
    from sandbox_helpers import FakeSandbox
    from test_runner import FakeAgent, task, writes_report_with_skill

    path = tmp_path / "s"
    path.mkdir()
    (path / "SKILL.md").write_text("# S\n", encoding="utf-8")
    agent = FakeAgent(writes_report_with_skill)
    agent.prewarm = lambda n: agent.__dict__.setdefault("asked", []).append(n)
    run_tasks(Skill(path=path, name="s", body="# S\n"), [task()], agent,
              config=EvaluationConfig(runs_per_task=2, max_parallel_runs=3), run_dir=tmp_path,
              sandbox=FakeSandbox())
    assert agent.asked == [3]  # min(max_parallel_runs, the 4 runs)


def test_evaluate_closes_its_agent_however_it_ends(tmp_path, monkeypatch):
    from metaskill import evaluate
    closed = []
    agent = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(evaluate, "_evaluate_once",
                        lambda *args: (args[-1].append(agent), 1 / 0))
    with pytest.raises(ZeroDivisionError):
        evaluate.evaluate_skill(tmp_path)
    assert closed == [True]


# --- Fixes from the review ------------------------------------------------------------


def test_closing_many_waiting_children_is_quick(agent):
    # close() runs before evaluate returns, inside its "Took" time. Each discard ran
    # `taskkill /T` (0.3 s on Windows): 16 children cost 4.9 s.
    agent.prewarm(8)
    assert wait_for(lambda: len(warm_pids(agent)) == 8)
    pids = warm_pids(agent)
    start = time.monotonic()
    agent.close()
    assert time.monotonic() - start < 2
    assert wait_for(lambda: not any(alive(p) for p in pids))


def test_an_exited_child_is_not_signalled_again(monkeypatch):
    # On POSIX a reaped child's pid, its group's id, can be another process's.
    signalled = []
    monkeypatch.setattr(hermes_agent, "_kill_tree", lambda proc, job=None: signalled.append(proc))
    monkeypatch.setattr(hermes_agent, "_windows", lambda: False)
    hermes_agent._kill(SimpleNamespace(returncode=0, pid=4242))
    assert signalled == []
    running = SimpleNamespace(returncode=None, pid=4243)
    hermes_agent._kill(running)
    assert signalled == [running]

    class Exited:
        pid, returncode = 4244, 0

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return None, None

    warm = hermes_agent._Warm(Exited(), None, SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(hermes_agent, "_kill", lambda *a: signalled.append("kill"))
    warm.discard()
    assert signalled == [running]  # discard() left it alone too


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_an_interrupted_refill_gives_its_slot_back(agent, monkeypatch):
    def interrupted(argv):
        raise KeyboardInterrupt
    monkeypatch.setattr(hermes_agent._Warm, "start", staticmethod(interrupted))
    agent.prewarm(2)
    agent._filler.join(5)
    # Or every later refill would think one was on its way, or the filler still running.
    assert agent._starting == 0 and agent._filling is False


# --- The stagger (WARM_STAGGER) ---------------------------------------------------------


def record_starts(monkeypatch):
    """Patch _Warm.start to note when each child starts."""
    starts, real = [], hermes_agent._Warm.start

    def start(argv):
        starts.append(time.monotonic())
        return real(argv)
    monkeypatch.setattr(hermes_agent._Warm, "start", staticmethod(start))
    return starts


def test_prewarm_returns_at_once_and_starts_children_a_stagger_apart(agent, monkeypatch):
    starts = record_starts(monkeypatch)
    agent.stagger = 0.2
    begin = time.monotonic()
    agent.prewarm(4)
    assert time.monotonic() - begin < 0.15  # the filler thread starts them, not the caller
    assert wait_for(lambda: len(agent._idle) == 4)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 4 and min(gaps) >= 0.15, gaps
    assert wait_for(lambda: not agent._filling)  # the filler ends once the pool is full
    assert not agent._filler.is_alive() or wait_for(lambda: not agent._filler.is_alive())


def test_sessions_wait_for_the_next_warm_child_rather_than_start_cold(agent):
    # Three sessions at once, with one child started so far: starting the other two cold
    # would import Hermes in two processes together, the burst the stagger is for.
    agent.stagger = 0.3
    agent.prewarm(3)
    results, lock = [], threading.Lock()

    def session():
        warm = agent._take()
        with lock:
            results.append(warm)

    threads = [threading.Thread(target=session) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert None not in results and len({id(w) for w in results}) == 3
    for warm in results:
        warm.discard()


def test_close_and_cancel_end_the_filler_mid_stagger(agent, monkeypatch):
    starts = record_starts(monkeypatch)
    agent.stagger = 5.0  # it would take 40 s to start them all
    agent.prewarm(8)
    assert wait_for(lambda: len(starts) == 1)
    begin = time.monotonic()
    agent.close()
    agent._filler.join(5)
    assert not agent._filler.is_alive() and time.monotonic() - begin < 2
    assert len(starts) == 1 and agent._idle == []

    agent.prewarm(8)
    assert wait_for(lambda: len(starts) == 2)
    agent.cancel()
    agent._filler.join(5)
    assert not agent._filler.is_alive() and len(starts) == 2


def test_a_session_does_not_wait_when_no_filler_is_running(agent, monkeypatch):
    def refuse(argv):
        raise OSError("Windows refused")
    monkeypatch.setattr(hermes_agent._Warm, "start", staticmethod(refuse))
    agent.stagger = 5.0
    agent.prewarm(4)  # the filler fails its first start and ends
    begin = time.monotonic()
    assert agent._take() is None
    assert time.monotonic() - begin < 2  # cold at once, not after _TAKE_WAIT
    assert wait_for(lambda: not agent._filling)
