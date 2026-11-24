"""Task sessions run in parallel (max_parallel_runs, runner.md 2.7): the results are those of
a sequential run, every container is removed on a failure or an interrupt, rate limits are
backed off, and the shared judges stay consistent. Fakes only: no Docker, no model."""

import functools
import random
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate import backoff as backoff_module
from metaskill.evaluate import hermes_agent, runner
from metaskill.evaluate.backoff import Backoff, is_rate_limited
from metaskill.evaluate.hermes_agent import HermesAgentUnavailable
from metaskill.evaluate.judge import Judge, JudgeCallFailed, JudgeError
from metaskill.evaluate.models import (
    MAX_PARALLEL_RUNS, Check, CheckKind, EvaluationConfig, JudgeType, Variant)
from metaskill.evaluate.runner import AgentRun, run_tasks
from metaskill.evaluate.sandbox import DockerSandbox
from sandbox_helpers import FakeSandbox
from test_runner import FakeAgent, FakeLlm, file_check, writes_report_with_skill
from test_runner import task as runner_task


def task(task_id="write-report", checks=None):
    """test_runner's task, with a prompt naming it, so a fake agent can tell tasks apart."""
    made = runner_task(task_id, checks=checks)
    made.prompt = f"task {task_id}"
    return made


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    monkeypatch.setattr(runner, "DockerSandbox", FakeSandbox)


@pytest.fixture
def skill(tmp_path):
    from metaskill.evaluate.models import Skill
    path = tmp_path / "cleanup"
    path.mkdir()
    (path / "SKILL.md").write_text("# Cleanup\n", encoding="utf-8")
    return Skill(path=path, name="cleanup", body="# Cleanup\n")


@pytest.fixture
def no_waiting(monkeypatch):
    """Backoff pauses are recorded, not slept: a fake clock that the fake sleep moves on,
    shared by every Backoff the test makes."""
    slept, now, lock = [], [0.0], threading.Lock()

    def sleep(seconds):
        with lock:
            slept.append(seconds)
            now[0] += seconds

    def clock():
        with lock:
            return now[0]

    monkeypatch.setattr(runner, "Backoff", functools.partial(Backoff, sleep=sleep, clock=clock))
    return slept


def config(runs=3, parallel=4):
    return EvaluationConfig(runs_per_task=runs, max_parallel_runs=parallel)


def jittery(prompt, skill, workdir):
    """Writes the report with the skill, and takes a random short time, so parallel runs
    finish out of order. Output and transcript name the task and variant, never the order."""
    time.sleep(random.uniform(0, 0.02))
    if skill is not None:
        (workdir / "report.txt").write_text("ok", encoding="utf-8")
    who = "skill" if skill else "baseline"
    return AgentRun(output=f"{prompt} by {who}", input_tokens=10, llm_calls=1, tool_calls=1,
                    transcript=f"{prompt}\n{who}", seconds=1.0)


def comparable(result):
    """A RunnerResult without its timing, which is all parallel runs may change."""
    tests = []
    for t in result.tests:
        record = t.to_dict()
        record["metrics"].pop("seconds")
        tests.append(record)
    return tests, result.errors, result.transcripts, result.task_ids


TASKS = [task("a"), task("b", checks=[file_check("missing.txt")]), task("c")]


# --- Same results as sequential -----------------------------------------------------


@pytest.mark.parametrize("parallel", [2, 3, MAX_PARALLEL_RUNS])
def test_parallel_results_equal_sequential_in_content_and_order(skill, tmp_path, parallel):
    sequential = run_tasks(skill, TASKS, FakeAgent(jittery), config=config(parallel=1),
                           run_dir=tmp_path / "seq")
    at_once = run_tasks(skill, TASKS, FakeAgent(jittery), config=config(parallel=parallel),
                        run_dir=tmp_path / "par")
    assert comparable(at_once) == comparable(sequential)
    assert [(t.task_id, t.variant, t.run) for t in at_once.tests] == [
        (tid, variant, n) for tid in "abc" for variant in (Variant.WITH_SKILL, Variant.BASELINE)
        for n in (1, 2, 3)]
    # One log per run, the same set of files with the same content.
    logs = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "par/cleanup/logs").iterdir()}
    assert logs.keys() == {p.name for p in (tmp_path / "seq/cleanup/logs").iterdir()}
    assert len(logs) == 18
    assert "task b by skill" in logs["b-with_skill-r2.txt"]


def test_sessions_really_run_at_once_up_to_the_limit(skill, tmp_path):
    lock, running, peak = threading.Lock(), [0], [0]

    def slow(prompt, skill, workdir):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return AgentRun(output="done")

    run_tasks(skill, [task("a"), task("b")], FakeAgent(slow), config=config(parallel=3),
              run_dir=tmp_path)
    assert peak[0] == 3


def test_parallel_runs_are_faster(skill, tmp_path):
    # The acceptance check in small: 8 sessions of 0.2 s take about 1.6 s one at a time and
    # about 0.4 s four at a time. The bound is loose so a slow CI machine still passes.
    def slow(prompt, skill, workdir):
        time.sleep(0.2)
        return AgentRun(output="done")

    tasks = [task("a"), task("b")]
    start = time.monotonic()
    run_tasks(skill, tasks, FakeAgent(slow), config=config(runs=2, parallel=1), run_dir=tmp_path / "s")
    sequential = time.monotonic() - start
    start = time.monotonic()
    run_tasks(skill, tasks, FakeAgent(slow), config=config(runs=2, parallel=4), run_dir=tmp_path / "p")
    parallel = time.monotonic() - start
    assert sequential >= 1.6 and parallel < sequential / 2


def test_the_sandbox_caps_how_many_run_at_once(skill, tmp_path):
    class SmallSandbox(FakeSandbox):
        def capacity(self):
            return 2

    lock, running, peak = threading.Lock(), [0], [0]

    def slow(prompt, skill, workdir):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return AgentRun(output="done")

    run_tasks(skill, [task("a"), task("b")], FakeAgent(slow), config=config(parallel=8),
              run_dir=tmp_path, sandbox=SmallSandbox())
    assert peak[0] == 2


def test_exit_code_checks_run_in_their_own_runs_workspace(skill, tmp_path):
    # Each run writes a file naming itself; its exit_code check passes only if it sees
    # exactly that file, so a check run in another run's workspace would fail.
    def marks(prompt, skill, workdir):
        time.sleep(random.uniform(0, 0.02))
        (workdir / f"{threading.get_ident()}.mark").write_text(prompt, encoding="utf-8")
        return AgentRun(output="done")

    def code(workdir):
        return 0 if len(list(workdir.glob("*.mark"))) == 1 else 1

    exit_check = Check(judge=JudgeType.DETERMINISTIC, kind=CheckKind.EXIT_CODE, target="check")
    result = run_tasks(skill, [task(t, checks=[exit_check]) for t in "abcd"], FakeAgent(marks),
                       config=config(parallel=4), run_dir=tmp_path, sandbox=FakeSandbox(code=code))
    assert result.errors == [] and all(t.passed for t in result.tests) and len(result.tests) == 24


# --- A failure in one run --------------------------------------------------------------


def test_a_failure_in_one_run_costs_that_run_only(skill, tmp_path):
    def one_bad(prompt, skill, workdir):
        if prompt == "task b" and skill is not None:
            raise HermesAgentUnavailable("Hermes could not be set up")  # an error, not a score
        return jittery(prompt, skill, workdir)

    sandbox = FakeSandbox()
    result = run_tasks(skill, TASKS, FakeAgent(one_bad), config=config(parallel=4),
                       run_dir=tmp_path, sandbox=sandbox)
    # The three lost runs are errors in plan order; every other run is a result, in order.
    assert result.errors == [f"b with_skill r{n}: HermesAgentUnavailable: Hermes could not be set up"
                             for n in (1, 2, 3)]
    assert [(t.task_id, t.variant.value) for t in result.tests] == [
        (tid, v) for tid in "abc" for v in ("with_skill", "baseline") for _ in range(3)
        if (tid, v) != ("b", "with_skill")]
    assert all(w.closed for w in sandbox.opened)


def test_an_unexpected_error_in_one_run_does_not_stop_the_others(skill, tmp_path):
    def broken_once(prompt, skill, workdir):
        if prompt == "task a" and skill is None:
            return AgentRun(output=None)  # not text: the runner raises TypeError for this run
        return jittery(prompt, skill, workdir)

    result = run_tasks(skill, TASKS, FakeAgent(broken_once), config=config(parallel=4),
                       run_dir=tmp_path)
    assert [e.split(":")[0] for e in result.errors] == ["a baseline r1", "a baseline r2",
                                                         "a baseline r3"]
    assert len(result.tests) == 15


# --- Cleanup on interrupt ----------------------------------------------------------------


class CancellableAgent(FakeAgent):
    def __init__(self, act):
        super().__init__(act)
        self.cancelled = 0

    def cancel(self):
        self.cancelled += 1


@pytest.mark.parametrize("parallel", [1, 4])
def test_an_interrupt_removes_every_container_and_stops_the_agent(skill, tmp_path, parallel):
    started = threading.Event()

    def interrupted(prompt, skill, workdir):
        if prompt == "task b":
            started.set()
            raise KeyboardInterrupt  # Ctrl-C, as a worker would see the run stop
        time.sleep(0.05)
        return AgentRun(output="done")

    agent, sandbox = CancellableAgent(interrupted), FakeSandbox()
    with pytest.raises(KeyboardInterrupt):
        run_tasks(skill, TASKS, agent, config=config(parallel=parallel), run_dir=tmp_path,
                  sandbox=sandbox)
    assert started.is_set()
    assert sandbox.opened and all(w.closed for w in sandbox.opened)
    assert agent.cancelled == 1
    # No run thread outlives the call, and no session started after the interrupt
    # reached past what was already running.
    assert not [t for t in threading.enumerate() if t.name.startswith("metaskill-run")]
    assert len(sandbox.opened) < 18


def test_a_workspace_opened_while_stopping_is_closed_at_once(skill, tmp_path):
    pool = runner._Judges(None, config(), None, FakeSandbox())
    sandbox = pool.sandbox
    workspace = pool.open([])
    pool.stop()
    assert workspace.closed
    with pytest.raises(JudgeError, match="stopped"):
        pool.open([])
    assert len(sandbox.opened) == 1


def test_a_stop_racing_an_open_still_closes_it(skill, tmp_path):
    class RacingSandbox(FakeSandbox):
        """stop() runs while the container is being made, after the pool's first check."""
        def open(self, fixtures):
            workspace = super().open(fixtures)
            pool.stop()
            return workspace

    pool = runner._Judges(None, config(), None, RacingSandbox())
    with pytest.raises(JudgeError, match="stopped"):
        pool.open([])
    assert pool.sandbox.opened[0].closed


# --- Rate limits -------------------------------------------------------------------------


def test_a_rate_limited_session_is_waited_out_and_run_again(skill, tmp_path, no_waiting):
    lock, with_skill_calls = threading.Lock(), []

    def limited_once(prompt, skill, workdir):
        if skill is not None:
            with lock:
                with_skill_calls.append(prompt)
                first = len(with_skill_calls) == 1
            if first:
                raise HermesAgentUnavailable("the model provider could not serve the session "
                                             "(rate_limit): 429", rate_limited=True)
        return writes_report_with_skill(prompt, skill, workdir)

    sandbox = FakeSandbox()
    result = run_tasks(skill, [task("a")], FakeAgent(limited_once), config=config(runs=1, parallel=2),
                       run_dir=tmp_path, sandbox=sandbox)
    assert result.errors == []
    assert [t.passed for t in result.tests] == [True, False]  # the retry passed
    assert len(with_skill_calls) == 2 and no_waiting  # one retry, after a pause
    assert all(w.closed for w in sandbox.opened) and len(sandbox.opened) == 3


def test_a_session_still_limited_after_every_attempt_is_an_error(skill, tmp_path, no_waiting):
    def always_limited(prompt, skill, workdir):
        raise HermesAgentUnavailable("rate_limit", rate_limited=True)

    agent = FakeAgent(always_limited)
    result = run_tasks(skill, [task("a")], agent, config=config(runs=1, parallel=1),
                       run_dir=tmp_path)
    assert len(result.errors) == 2 and result.tests == []
    assert len(agent.calls) == 2 * backoff_module.ATTEMPTS
    # The pause doubles while the limits keep coming.
    assert no_waiting[:3] == [5.0, 10.0, 20.0]


def test_other_provider_failures_are_not_retried(skill, tmp_path, no_waiting):
    agent = FakeAgent(lambda p, s, w: (_ for _ in ()).throw(HermesAgentUnavailable("billing")))
    result = run_tasks(skill, [task("a")], agent, config=config(runs=1, parallel=1), run_dir=tmp_path)
    assert len(agent.calls) == 2 and len(result.errors) == 2 and no_waiting == []


def test_a_hermes_rate_limit_is_flagged_and_billing_is_not():
    with pytest.raises(HermesAgentUnavailable) as limited:
        hermes_agent._agent_run({"status": "provider_error", "failure_reason": "rate_limit"})
    assert limited.value.rate_limited is True
    with pytest.raises(HermesAgentUnavailable) as billing:
        hermes_agent._agent_run({"status": "provider_error", "failure_reason": "billing"})
    assert billing.value.rate_limited is False
    with pytest.raises(HermesAgentUnavailable) as odd:
        hermes_agent._agent_run({"status": "provider_error", "failure_reason": ["rate_limit"]})
    assert odd.value.rate_limited is False


class RateLimitError(Exception):
    """Named like the provider SDKs' error."""


@pytest.mark.parametrize("error, limited", [
    (RateLimitError("slow down"), True),
    (RuntimeError("HTTP 429 Too Many Requests"), True),
    (SimpleNamespace(), False),
    (RuntimeError("model not found"), False),
    (ValueError("Rate limit reached for requests"), True),
])
def test_rate_limits_are_recognised(error, limited):
    if not isinstance(error, BaseException):
        error = RuntimeError("boom")
        error.status_code = 429
        limited = True
    assert is_rate_limited(error) is limited


def test_a_rate_limit_raised_from_is_recognised_and_a_flag_is_taken_at_its_word():
    try:
        try:
            raise RateLimitError("slow down")
        except RateLimitError as e:
            raise JudgeCallFailed("judge request failed") from e
    except JudgeCallFailed as wrapped:
        assert is_rate_limited(wrapped)
    flagged = HermesAgentUnavailable("the provider said: rate limit", rate_limited=False)
    assert not is_rate_limited(flagged)  # its message is not read


def test_backoff_pauses_every_waiter_doubles_and_resets():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    gate = Backoff(initial=5, maximum=12, sleep=sleep, clock=lambda: now[0])
    gate.wait()
    assert slept == []
    assert [gate.limited(), gate.limited(), gate.limited()] == [5, 10, 12]  # capped
    gate.wait()
    assert now[0] == 12 and gate.limits == 3
    gate.succeeded()
    assert gate.limited() == 5


def test_the_llm_judge_of_task_checks_backs_off_a_rate_limit(no_waiting):
    class LimitedOnce(FakeLlm):
        def complete_structured(self, **kwargs):
            if not getattr(self, "limited", False):
                self.limited = True
                raise RateLimitError("429")
            return super().complete_structured(**kwargs)

    reply = {"items": [{"item": 1, "met": True, "reason": "", "quote": "wrote the report to report.txt"}]}
    judge = Judge(LimitedOnce(reply), retries=0)
    judge.backoff = runner.Backoff()
    run = SimpleNamespace(text="wrote the report to report.txt", workdir=Path("."))
    check = Check(judge=JudgeType.LLM, checklist=["says it wrote the report"])
    verdict = judge.judge(task(checks=[check]), check, run)
    assert verdict.passed and no_waiting


def test_a_judge_without_backoff_fails_a_rate_limit_at_once():
    class Limited(FakeLlm):
        def complete_structured(self, **kwargs):
            raise RateLimitError("429")

    judge = Judge(Limited(), retries=0)
    with pytest.raises(JudgeCallFailed):
        judge.ask("x", "y", {"type": "object"}, "check")


# --- Shared judges and settings -----------------------------------------------------------


def test_the_llm_judges_records_add_up_across_threads():
    class Many(FakeLlm):
        def complete_structured(self, **kwargs):
            return super().complete_structured(**kwargs) if False else SimpleNamespace(
                provider="fake", model="m", text="{}", parsed={}, content_type="json",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    judge = Judge(Many(), retries=0)
    threads = [threading.Thread(target=lambda: [judge.ask("x", "y", {"type": "object"}, "check")
                                                for _ in range(200)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert judge.total_usage.calls == 1600 and judge.model_calls == {"fake/m": 1600}


@pytest.mark.parametrize("value", [0, MAX_PARALLEL_RUNS + 1, 1.5, True, "2"])
def test_max_parallel_runs_must_be_a_small_whole_number(value):
    with pytest.raises(ValueError, match="max_parallel_runs"):
        EvaluationConfig(max_parallel_runs=value)


def test_max_parallel_runs_round_trips_and_does_not_change_the_hash():
    config = EvaluationConfig.from_dict({"max_parallel_runs": 5})
    assert config.max_parallel_runs == 5
    assert EvaluationConfig.from_dict(config.to_dict()).max_parallel_runs == 5
    assert config.hash() == EvaluationConfig().hash()


def test_docker_capacity_is_cpus_and_memory_whichever_is_fewer():
    sandbox = DockerSandbox()

    def info(*args):
        return SimpleNamespace(returncode=0, stdout=f"{info.cpus} {info.memory}\n", stderr="")

    sandbox._docker = info
    info.cpus, info.memory = 8, 2 * 1024 ** 3  # 2 GB: four 512 MB sandboxes
    assert sandbox.capacity() == 4
    info.cpus, info.memory = 2, 64 * 1024 ** 3
    assert sandbox.capacity() == 2
    info.cpus, info.memory = 1, 100  # less than one sandbox's memory: still one at a time
    assert sandbox.capacity() == 1
    sandbox._docker = lambda *a: SimpleNamespace(returncode=1, stdout="", stderr="no daemon")
    assert sandbox.capacity() is None


# --- The Hermes agent stops its sessions --------------------------------------------------


class FakeProc:
    pid = 4242

    def __init__(self, interrupt=False):
        self.interrupt, self.returncode = interrupt, 0

    def communicate(self, timeout=None):
        if self.interrupt:
            raise KeyboardInterrupt
        return "", ""


@pytest.fixture
def posix_child(monkeypatch):
    """_run_child as on POSIX, whatever this OS: a fake child must not reach winjob."""
    monkeypatch.setattr(hermes_agent, "_windows", lambda: False)


def test_an_interrupted_hermes_session_is_killed(monkeypatch, posix_child):
    killed, tracked = [], []
    monkeypatch.setattr(hermes_agent.subprocess, "Popen", lambda *a, **k: FakeProc(interrupt=True))
    monkeypatch.setattr(hermes_agent, "_kill", lambda proc, job=None: killed.append(proc))
    with pytest.raises(KeyboardInterrupt):
        hermes_agent._run_child(["hermes"], 10,
                                track=lambda p, running, job: tracked.append(running))
    assert len(killed) == 1 and tracked == [True, False]


def test_an_interrupted_windows_session_is_killed_with_its_job(monkeypatch):
    # cancel() and Ctrl-C stop a Windows session's whole tree, as a timeout does: with its job.
    killed, tracked, job_closed = [], [], []
    job = SimpleNamespace(close=lambda: job_closed.append(True))
    monkeypatch.setattr(hermes_agent, "_windows", lambda: True)
    monkeypatch.setattr(hermes_agent.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(hermes_agent.subprocess, "Popen", lambda *a, **k: FakeProc(interrupt=True))
    monkeypatch.setattr(hermes_agent, "_contain", lambda proc: job)
    monkeypatch.setattr(hermes_agent, "_kill", lambda proc, j=None: killed.append(j))
    with pytest.raises(KeyboardInterrupt):
        hermes_agent._run_child(["hermes"], 10,
                                track=lambda p, running, j: tracked.append((running, j)))
    assert killed == [job] and tracked == [(True, job), (False, job)] and job_closed


def test_cancel_kills_running_sessions_and_refuses_new_ones(monkeypatch):
    killed = []
    monkeypatch.setattr(hermes_agent, "_kill", lambda proc, job=None: killed.append((proc, job)))
    agent = hermes_agent.HermesAgent()
    running, job = FakeProc(), object()
    agent._track(running, True, job)
    agent.cancel()
    assert killed == [(running, job)]  # its whole tree, with its job on Windows
    late = FakeProc()
    agent._track(late, True)  # started just as the stop came: stopped at once
    assert killed == [(running, job), (late, None)]
    with pytest.raises(HermesAgentUnavailable, match="stopped"):
        agent.run("p", None, SimpleNamespace())
