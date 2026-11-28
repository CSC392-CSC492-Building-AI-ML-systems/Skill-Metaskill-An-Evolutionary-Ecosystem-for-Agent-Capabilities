"""Task sessions run in parallel (max_parallel_runs, runner.md 2.7): the results are those of
a sequential run, every container is removed on a failure or an interrupt, rate limits are
backed off, and the shared judges stay consistent. Fakes only: no Docker, no model."""

import contextvars
import functools
import json
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
from metaskill.evaluate.judge import Judge, JudgeCallFailed, JudgeError, Verdict
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

    monkeypatch.setattr(runner, "Backoff",
                        functools.partial(Backoff, sleep=sleep, clock=clock, jitter=0))
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
    logs = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "par/skill-01/logs").iterdir()}
    assert logs.keys() == {p.name for p in (tmp_path / "seq/skill-01/logs").iterdir()}
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

    gate = Backoff(initial=5, maximum=12, sleep=sleep, clock=lambda: now[0], jitter=0)
    gate.wait()
    assert slept == []
    pauses = []
    for _ in range(3):  # limited again each time the pause ends
        pauses.append(gate.limited())
        gate.wait()
    assert pauses == [5, 10, 12] and now[0] == 27 and gate.limits == 3  # capped at 12
    gate.succeeded()
    assert gate.limited() == 5


def test_a_burst_of_limits_in_one_pause_does_not_double_it():
    # 16 workers limited by one provider window, at the same moment: one 5 s pause, not 60 s.
    now = [0.0]
    gate = Backoff(initial=5, maximum=60, sleep=lambda s: now.__setitem__(0, now[0] + s),
                   clock=lambda: now[0], jitter=0)
    assert [gate.limited() for _ in range(16)] == [5] * 16
    gate.wait()
    assert now[0] == 5 and gate.limits == 16
    assert gate.limited() == 10  # limited again after the pause: now it doubles


def test_endless_limits_cannot_overflow_the_doubling():
    now = [0.0]
    gate = Backoff(initial=5, maximum=60, sleep=lambda s: now.__setitem__(0, now[0] + s),
                   clock=lambda: now[0], jitter=0)
    for _ in range(2000):  # 2 ** 1024 used to raise OverflowError, ending the whole skill
        gate.limited()
        gate.wait()
    assert gate.limited() == 60


def test_workers_wake_at_different_times_after_a_pause():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
    gate = Backoff(initial=5, sleep=sleep, clock=lambda: now[0], jitter=1.0)
    gate.limited()
    gate.wait()
    assert slept[0] == 5 and len(slept) == 2 and 0 <= slept[1] <= 1.0
    gate.wait()  # no pause in force: no jitter either
    assert len(slept) == 2


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


@pytest.mark.parametrize("cpus, memory, room", [
    (8, 2 * 1024 ** 3, 4),  # 2 GB: four 512 MB sandboxes
    (2, 64 * 1024 ** 3, 2),
    (1, 100, 1),  # less than one sandbox's memory: still one at a time
])
def test_docker_capacity_is_cpus_and_memory_whichever_is_fewer(cpus, memory, room):
    sandbox, calls = DockerSandbox(), []

    def info(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=f"{cpus} {memory}\n", stderr="")

    sandbox._docker = info
    assert sandbox.capacity() == room
    assert sandbox.capacity() == room and len(calls) == 1  # asked once, then kept


def test_docker_capacity_is_none_when_docker_does_not_say_and_is_asked_again():
    sandbox, calls = DockerSandbox(), []

    def no_daemon(*args):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="no daemon")

    sandbox._docker = no_daemon
    assert sandbox.capacity() is None and sandbox.capacity() is None
    assert len(calls) == 2  # an unknown answer is not kept


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


# --- Stopping ends rate-limit pauses, and the agent takes sessions again --------------------


def test_cancel_wakes_a_worker_in_a_rate_limit_pause_at_once():
    gate = Backoff(initial=60)
    gate.limited()  # a 60 s pause
    waiter = threading.Thread(target=gate.wait)
    start = time.monotonic()
    waiter.start()
    time.sleep(0.05)
    gate.cancel()
    waiter.join(timeout=5)
    assert not waiter.is_alive() and time.monotonic() - start < 5
    gate.wait()  # and every later pause is over at once
    assert gate.cancelled


def test_linked_backoffs_share_a_pause_but_not_a_cancel():
    now = [0.0]
    sleep = lambda s: now.__setitem__(0, now[0] + s)  # noqa: E731
    call = Backoff(initial=5, sleep=sleep, clock=lambda: now[0], jitter=0)
    runs = call.linked()
    assert runs.limited() == 5  # the runs hit a limit ...
    call.wait()  # ... and the call's other model use waits it out too
    assert now[0] == 5 and call.limits == 1
    runs.cancel()  # a runner call stopping (e.g. one that failed) ...
    assert runs.cancelled and not call.cancelled  # ... leaves the later skills' scoring be
    assert call.linked().limited() == 10  # still one pause: it doubles across views


def test_call_retries_a_rate_limit_and_makes_no_call_once_cancelled():
    gate = Backoff(initial=1, sleep=lambda s: None, jitter=0)
    replies = [RateLimitError("429"), RateLimitError("429"), "ok"]

    def fn():
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    assert gate.call(fn) == "ok" and gate.limits == 2
    with pytest.raises(ValueError):  # not a rate limit: raised at once
        gate.call(lambda: (_ for _ in ()).throw(ValueError("bad")))
    gate.cancel()
    with pytest.raises(backoff_module.BackoffCancelled):
        gate.call(lambda: pytest.fail("called after cancel"))


def test_call_gives_up_after_its_attempts():
    gate = Backoff(initial=1, attempts=3, sleep=lambda s: None, jitter=0)
    tries = []

    def limited():
        tries.append(1)
        raise RateLimitError("429")
    with pytest.raises(RateLimitError):
        gate.call(limited)
    assert len(tries) == 3


def test_a_backed_off_llm_retries_ctx_llm_and_passes_the_rest_through():
    class Llm:
        name = "ctx.llm"
        calls = 0

        def complete_structured(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError("429")
            return kwargs
    inner = Llm()
    llm = backoff_module.BackedOffLlm(inner, Backoff(initial=1, sleep=lambda s: None, jitter=0))
    assert llm.complete_structured(task="t") == {"task": "t"} and inner.calls == 2
    assert llm.name == "ctx.llm"


def test_stopping_the_pool_ends_its_pause_and_no_judge_call_is_made_after():
    pool = runner._Judges(None, config(), None, FakeSandbox())
    pool.backoff.limited()
    pool.stop()
    assert pool.backoff.cancelled

    class Never(FakeLlm):
        def complete_structured(self, **kwargs):
            raise AssertionError("a judge call was made after the stop")

    judge = Judge(Never(), retries=0)
    judge.backoff = pool.backoff
    with pytest.raises(JudgeError, match="stopped"):
        judge.ask("x", "y", {"type": "object"}, "check")


def test_a_new_runner_call_resumes_an_agent_an_earlier_stop_cancelled(skill, tmp_path):
    class Resumable(CancellableAgent):
        resumed = 0

        def resume(self):
            self.resumed += 1

    agent = Resumable(writes_report_with_skill)
    run_tasks(skill, [task("a")], agent, config=config(runs=1), run_dir=tmp_path)
    assert agent.resumed == 1 and agent.cancelled == 0


def test_hermes_agent_resume_takes_sessions_again(monkeypatch):
    monkeypatch.setattr(hermes_agent, "_kill", lambda proc, job=None: None)
    agent = hermes_agent.HermesAgent()
    agent.cancel()
    agent.resume()
    killed = []
    monkeypatch.setattr(hermes_agent, "_kill", lambda proc, job=None: killed.append(proc))
    agent._track(FakeProc(), True)  # not stopped: the agent is no longer cancelled
    assert killed == [] and not agent._cancelled


def test_the_runtime_command_lookup_does_not_hold_the_session_lock(monkeypatch):
    # cancel() and a session ending (_track) take the session lock; the `hermes` lookup of
    # the runtime command must not keep them waiting.
    agent = hermes_agent.HermesAgent()
    inside, release = threading.Event(), threading.Event()

    def slow_lookup():
        inside.set()
        release.wait(5)
        return ["python", "-c", "x"]

    monkeypatch.setattr(agent, "_runtime_argv_locked", slow_lookup)
    looker = threading.Thread(target=agent._runtime_argv)
    looker.start()
    assert inside.wait(5)
    try:
        assert agent._lock.acquire(timeout=1)
        agent._lock.release()
    finally:
        release.set()
        looker.join(5)


def test_an_interrupted_child_is_reaped_after_it_is_killed(monkeypatch, posix_child):
    class Proc(FakeProc):
        def __init__(self):
            super().__init__()
            self.calls = []

        def communicate(self, timeout=None):
            self.calls.append(timeout)
            if len(self.calls) == 1:
                raise KeyboardInterrupt
            return "", ""

    proc = Proc()
    monkeypatch.setattr(hermes_agent.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(hermes_agent, "_kill", lambda p, job=None: None)
    with pytest.raises(KeyboardInterrupt):
        hermes_agent._run_child(["hermes"], 10)
    assert proc.calls == [10, hermes_agent._REAP_TIMEOUT]


# --- A run's LLM checks are asked at once -------------------------------------------------


def llm_check(item):
    return Check(judge=JudgeType.LLM, checklist=[item])


class SlowJudge:
    """An LLM judge whose verdict names its check, after a pause; counts calls at once."""

    judge_type = JudgeType.LLM

    def __init__(self, fail=()):
        self.lock, self.running, self.peak, self.fail = threading.Lock(), 0, 0, set(fail)

    def judge(self, task, check, run):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(0.1)
        with self.lock:
            self.running -= 1
        if check.checklist[0] in self.fail:
            raise JudgeError(f"failed {check.checklist[0]}")
        return Verdict(passed=True, score=1.0, reason=check.checklist[0], evidence=None)


def test_a_runs_llm_checks_are_asked_at_once_and_kept_in_order(skill, tmp_path):
    slow = SlowJudge()
    checks = [llm_check("one"), file_check(), llm_check("two"), llm_check("three")]
    result = run_tasks(skill, [task("a", checks=checks)], FakeAgent(writes_report_with_skill),
                       config=config(runs=1, parallel=1), run_dir=tmp_path,
                       judges={JudgeType.LLM: slow})
    reasons = result.tests[0].reason.split("; ")
    assert slow.peak == 3
    assert (reasons[0], reasons[2], reasons[3]) == ("one", "two", "three")


def test_the_first_failing_check_in_order_is_the_runs_error(skill, tmp_path):
    slow = SlowJudge(fail={"two", "three"})
    checks = [llm_check("one"), llm_check("two"), llm_check("three")]
    result = run_tasks(skill, [task("a", checks=checks)], FakeAgent(writes_report_with_skill),
                       config=config(runs=1, parallel=1), run_dir=tmp_path,
                       judges={JudgeType.LLM: slow})
    assert result.tests == [] and len(result.errors) == 2
    assert all("failed two" in e for e in result.errors)


# --- Threads keep the caller's context (Hermes' profile secret scope) ------------------------

PROFILE = contextvars.ContextVar("PROFILE", default=None)


class ScopedLlm(FakeLlm):
    """Like Hermes on a multiplexing gateway: a call made outside the caller's profile scope
    (a contextvar) is refused, as UnscopedSecretError refuses ANTHROPIC_TOKEN."""

    def complete_structured(self, **kwargs):
        if PROFILE.get() != "user":
            raise RuntimeError("Hermes could not read this profile's ANTHROPIC_TOKEN")
        return SimpleNamespace(
            provider="fake", model="m", content_type="text", parsed=None,
            text=json.dumps({"items": [{"item": 1, "met": True, "reason": "",
                                        "quote": "wrote the report to report.txt"}]}),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.mark.parametrize("checks", [1, 2])  # 2: the run's checks go to threads of their own
def test_judge_calls_on_worker_threads_keep_the_callers_profile_scope(skill, tmp_path, checks):
    ctx = SimpleNamespace(llm=ScopedLlm())
    token = PROFILE.set("user")
    try:
        result = run_tasks(skill, [task("a", checks=[llm_check("says it wrote")] * checks)],
                           FakeAgent(writes_report_with_skill), config=config(runs=2, parallel=4),
                           run_dir=tmp_path, ctx=ctx)
    finally:
        PROFILE.reset(token)
    assert result.errors == [] and len(result.tests) == 4


def test_submit_runs_in_a_copy_of_the_callers_context():
    from concurrent.futures import ThreadPoolExecutor
    from metaskill.evaluate.threads import map_in_context, submit
    token = PROFILE.set("user")
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            assert submit(executor, PROFILE.get).result() == "user"
            assert map_in_context(executor, lambda _: PROFILE.get(), range(8)) == ["user"] * 8
            assert executor.submit(PROFILE.get).result() is None  # what the bug was
    finally:
        PROFILE.reset(token)


def test_a_rate_limit_after_a_view_was_cancelled_does_not_lengthen_the_shared_pause():
    """A runner's view cancelled while its call was in flight: the call's 429 is raised, not
    retried, and the pause the other views (evaluate's background scoring) share stays."""
    from metaskill.evaluate.backoff import Backoff

    shared = Backoff(sleep=lambda s: None, jitter=0)
    view = shared.linked()

    def limited_after_cancel():
        view.cancel()
        raise RuntimeError("429 Too Many Requests")

    with pytest.raises(RuntimeError, match="429"):
        view.call(limited_after_cancel)
    assert shared.limits == 0 and not shared.cancelled


def test_a_judge_whose_backoff_was_cancelled_does_not_lengthen_the_shared_pause():
    from metaskill.evaluate.backoff import Backoff
    from metaskill.evaluate.judge import JudgeCallFailed

    shared = Backoff(sleep=lambda s: None, jitter=0)
    from metaskill.evaluate.judge import Judge
    judge = Judge.__new__(Judge)
    judge.backoff = shared.linked()

    def limited_after_cancel(*args):
        judge.backoff.cancel()
        raise JudgeCallFailed("429 Too Many Requests")

    judge._complete_once = limited_after_cancel
    with pytest.raises(JudgeCallFailed):
        judge._complete("i", "m", "s", "p", None, None, None)
    assert shared.limits == 0
