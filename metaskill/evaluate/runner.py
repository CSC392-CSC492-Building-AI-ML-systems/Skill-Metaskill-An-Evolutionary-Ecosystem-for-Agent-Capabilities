"""Task runner: runs each task n times with and without the skill (docs/evaluate.md 4).

The only module that executes the agent. For every task, variant (`with_skill`,
`baseline`) and run 1..n it gives the agent a fresh sandboxed workspace with the task's
fixtures, judges the result with judge.py, writes the full log to the report folder and
records a TestResult. Quality, robustness and efficiency are computed from those results;
robustness.score_from_runner takes a RunnerResult as it is.

How an agent session is started is behind `Agent`, so tests use a fake one and no
Hermes internals are imported here. Everything the agent runs, and the command of every
`exit_code` check, runs in the run's `Workspace` (sandbox.py), never on this machine.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from . import cancel, host
from .backoff import Backoff
from .threads import submit
from .judge import (
    MAX_REASON_CHARS,
    DeterministicJudge,
    Judge,
    JudgeError,
    JudgeUnavailable,
    RunOutput,
    TaskJudge,
    TokenUsage,
    Verdict,
    clean,
    make_judge,
)
from .models import (
    EvaluationConfig, JudgeType, Metrics, Skill, Task, TestResult, Variant)
from .sandbox import DockerSandbox, RunFailed, Sandbox, Workspace
from .tasks import SAFE_NAME, load_tasks

VARIANTS = (Variant.WITH_SKILL, Variant.BASELINE)
_log = logging.getLogger(__name__)
# run_tasks(host_room=) left out: read the host's memory there (host.capacity).
MEASURE: Any = object()


@dataclass
class AgentRun:
    """What one agent session returned.

    The token and call fields are the usage of the agent's own task turns, summed over
    the session (one PluginLlmUsage per model reply; the agent aggregates across turns).
    They EXCLUDE host auxiliary calls -- title generation, summarization and the like --
    which happen regardless of the skill and must not count toward its efficiency. In a
    `hermes -z --usage-file` report these are the top-level counts, not the `auxiliary`
    block. Efficiency scores a cache-agnostic input total from these (docs/evaluate-efficiency.md).
    """

    output: str  # the agent's final reply
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # reasoning-model generation cost, billed like output
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_calls: int = 0  # model round-trips the session made
    tool_calls: int | None = 0  # None when the agent could not measure its tool calls
    # The full session, if the agent provides one: written to the log, and for with_skill
    # runs handed to robustness for its method agreement (RunnerResult.transcripts).
    transcript: str = ""
    # The session's own time, when the agent measured it. The runner's clock also counts the
    # agent's start-up (Hermes takes a second or two); this leaves that out. None: the runner's.
    seconds: float | None = None


def agent_run_from_hermes_usage(output: str, usage: dict[str, Any],
                                transcript: str = "") -> AgentRun:
    """Build an AgentRun from a `hermes -z --usage-file` report's TOP-LEVEL usage.

    Those counts are the agent's own task turns; the report's `auxiliary` block (title
    generation and other host side-tasks) is deliberately not read, so it never counts
    toward the skill (see AgentRun above). `api_calls` maps to llm_calls. tool_calls is not
    in the usage file, so it is None (not measured) and efficiency leaves that signal out
    rather than scoring it as zero; a real agent counts its own tool calls.
    """
    def n(key: str) -> int:
        return int(usage.get(key, 0) or 0)

    return AgentRun(
        output=output,
        input_tokens=n("input_tokens"),
        output_tokens=n("output_tokens"),
        reasoning_tokens=n("reasoning_tokens"),
        cache_read_tokens=n("cache_read_tokens"),
        cache_write_tokens=n("cache_write_tokens"),
        llm_calls=n("api_calls"),
        tool_calls=None,
        transcript=transcript,
    )


class Agent(Protocol):
    """Starts one agent session. `skill` is None for the baseline run.

    The session itself (its model calls) runs on this machine, but every command it
    runs and every file it reads or writes goes through `workspace`, the run's sandbox,
    whose `root` is its working folder. For a with_skill run the runner has already
    copied the skill (SKILL.md and its bundled files) into `workspace.skill_dir`. The
    Hermes agent reaches the workspace with a terminal and file backend of its own that
    calls it, not with Hermes' Docker backend, so every isolation flag stays in sandbox.py.
    """

    def run(self, prompt: str, skill: Skill | None, workspace: Workspace) -> AgentRun: ...


@dataclass
class RunnerResult:
    """Everything the runner produced for one skill."""

    tests: list[TestResult]  # the report's `tests` (evaluate.md 2.1)
    # The planned tasks' ids, in order, whether or not any of their runs made it into
    # `tests`, so a scorer can count a task that never ran (Quality's assess needs them).
    task_ids: list[str] = field(default_factory=list)
    # Runs that could not be judged (judge unavailable or failed, or a broken check).
    # They are not in `tests`, so a missing judge does not count as a failed task.
    errors: list[str] = field(default_factory=list)
    judge_model: str = ""  # for ReportMeta.judge_model; empty if no LLM check ran
    judge_usage: TokenUsage = field(default_factory=TokenUsage)
    # (task_id, run) -> the agent's transcript, for with_skill runs in `tests` whose agent
    # gave one (robustness's method agreement). Kept in memory only: like the logs, never
    # written to the report, since a transcript can hold anything the session read.
    transcripts: dict[tuple[str, int], str] = field(default_factory=dict)


def timestamp(now: datetime | None = None) -> str:
    """Run folder name and ReportMeta.timestamp, e.g. 2026-10-14-T15-30-02."""
    return (now or datetime.now()).strftime("%Y-%m-%d-T%H-%M-%S")


def report_dir(config: EvaluationConfig, now: datetime | None = None) -> Path:
    """`evaluation-reports/[timestamp]/`, the folder one evaluate or rank call writes."""
    return config.output_dir / timestamp(now)


def run_skill(skill: Skill, tasks_file: str | Path, agent: Agent, *,
              config: EvaluationConfig, run_dir: Path, ctx: Any = None,
              sandbox: Sandbox | None = None) -> RunnerResult:
    """Load the skill's tasks with tasks.py and run them (run_tasks)."""
    return run_tasks(skill, load_tasks(tasks_file), agent,
                     config=config, run_dir=run_dir, ctx=ctx, sandbox=sandbox)


def run_tasks(skill: Skill, tasks: list[Task], agent: Agent, *,
              config: EvaluationConfig, run_dir: Path, ctx: Any = None,
              judges: Mapping[JudgeType, TaskJudge] | None = None,
              sandbox: Sandbox | None = None, log_folder: str | None = None,
              backoff: Backoff | None = None,
              host_room: int | None | object = MEASURE) -> RunnerResult:
    """Run every task `config.runs_per_task` times per variant and judge each run.

    `ctx` is the Hermes plugin context, needed only for LLM checks. `sandbox` opens the
    workspace each run happens in, and runs its `exit_code` commands; None means a
    `DockerSandbox` whose run folders are `config.sandbox_disk` in size. `judges`
    replaces the judges built from these, for tests. Logs go to
    `run_dir/[log_folder]/logs/`; without a `log_folder`, the first `skill-01`,
    `skill-02`, ... that run_dir does not hold yet, so one call never overwrites
    another's logs. The skill's name is never used as a folder: it comes from the
    skill's own frontmatter. `backoff` is the caller's rate-limit pause, when other model
    calls share the provider with these runs (evaluate's background scoring); the runs wait
    on it through a view of their own (Backoff.linked), so stopping them does not cancel it
    for the caller. None: a new one. `host_room` is how many sessions the host's memory
    holds (host.capacity), None if unknown; evaluate reads it once, before its warm children
    start, since they would count as used. Left out, it is read here.

    Raises ValueError if log_folder is not a plain folder name.
    """
    if log_folder is not None and (not isinstance(log_folder, str)
                                   or not SAFE_NAME.fullmatch(log_folder)):
        raise ValueError(f"log folder {log_folder!r} may only contain letters, digits, "
                         "'_', '.' and '-'")
    pool = _Judges(ctx, config, judges, sandbox or DockerSandbox(disk=config.sandbox_disk),
                   backoff.linked() if backoff is not None else None)
    # One agent serves every skill of a rank call. A stop() in an earlier call cancelled it
    # (HermesAgent.cancel); if its caller went on to this skill, this skill's runs must not
    # all be refused as "stopped".
    resume = getattr(agent, "resume", None)
    if callable(resume):
        resume()
    result = RunnerResult(tests=[], task_ids=[task.task_id for task in tasks])
    folder = log_folder or _free_folder(run_dir)
    log_dir = run_dir / folder / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Every run, in the order the results are reported: task, variant, run number. Runs may
    # finish in any order (max_parallel_runs), so each outcome is kept at its place in the
    # plan, and the result is put together from the plan, as a sequential run gives it.
    plan = [_Planned(task, variant, n) for task in tasks for variant in VARIANTS
            for n in range(1, config.runs_per_task + 1)]
    outcomes: list[_Outcome | None] = [None] * len(plan)
    todo = []
    for index, planned in enumerate(plan):
        # Asked here, once and in order, before any session: the same errors in the same
        # order as a sequential run, and the LLM judge is built before the workers share it.
        unavailable = pool.unavailable(planned.task)
        if unavailable:
            # Not worth an agent session that cannot be judged.
            outcomes[index] = _Outcome(
                error=clean(f"{planned.label}: {unavailable}", MAX_REASON_CHARS))
        else:
            todo.append(index)

    def attempt(index: int) -> _Outcome:
        return _attempt(plan[index], skill, agent, pool, config, run_dir, folder)

    stopped: list[bool] = []
    stop_lock = threading.Lock()
    stop_done = threading.Event()

    def stop() -> None:
        # Interrupted (Ctrl-C), stopped (/metaskill stop, cancel.py) or failed past every
        # run's own handling: no new session starts, every container still open is removed,
        # and the agent stops what it runs. Once, under a lock: the stop watcher's thread
        # and this one can both get here. The second waits for the first to finish, so
        # the call does not return while the watcher is still removing containers.
        with stop_lock:
            first = not stopped
            stopped.append(True)
        if not first:
            stop_done.wait()
            return
        try:
            _stop_once()
        finally:
            stop_done.set()

    def _stop_once() -> None:
        # No new session or judge call first, then the sessions themselves, and only then
        # the containers: removing up to 16 takes seconds, during which live sessions would
        # go on calling the model against containers that are gone.
        pool.halt()
        cancel = getattr(agent, "cancel", None)
        if callable(cancel):
            cancel()
        pool.stop()

    # Read at the moment the count is decided, so a max_parallel_runs changed during a call
    # is held to the same caps.
    room = host.capacity() if host_room is MEASURE else host_room
    workers = pool.workers(config.max_parallel_runs, len(todo), room)
    # An agent whose sessions must run in plan order says so (the Quality pilot's route
    # check: the first run in the plan fixes the route, as before parallel runs).
    limit = getattr(agent, "max_parallel_runs", None)
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
        workers = min(workers, limit)
    # An agent that can start its sessions' processes ahead (HermesAgent.prewarm) keeps one
    # ready per worker, so a session does not wait for Hermes to start.
    prewarm = getattr(agent, "prewarm", None)
    cancel.check()  # stopped before this skill's runs began
    if callable(prewarm) and todo:
        prewarm(workers)
    # Ctrl-C does not reach an evaluation Hermes runs in its gateway: /metaskill stop, or
    # Hermes interrupting the turn, is seen by this watcher, which stops the runs as Ctrl-C
    # would. Its own thread: every runner thread may be blocked in a session.
    watcher = cancel.watch(stop)
    try:
        if workers <= 1:
            for index in todo:
                cancel.check()
                outcomes[index] = attempt(index)
        else:
            _run_parallel(todo, workers, attempt, outcomes, stop)
        # The runs a stop cut short ended as errors; they are not a result to report.
        cancel.check()
    except BaseException:
        stop()
        raise
    finally:
        if watcher is not None:
            watcher.close()

    for planned, outcome in zip(plan, outcomes):
        assert outcome is not None  # every planned run was decided above
        if outcome.error:
            result.errors.append(outcome.error)
            continue
        result.tests.append(outcome.test)
        if outcome.transcript and planned.variant is Variant.WITH_SKILL:
            result.transcripts[(planned.task.task_id, planned.n)] = outcome.transcript

    if pool.llm is not None:
        result.judge_model = pool.llm.judge_model
        result.judge_usage = pool.llm.total_usage
    return result


def _free_folder(run_dir: Path) -> str:
    """Make and return the first `skill-NN` folder run_dir does not have."""
    run_dir.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        folder = f"skill-{number:02d}"
        try:
            (run_dir / folder).mkdir()
            return folder
        except FileExistsError:
            number += 1


@dataclass(frozen=True)
class _Planned:
    """One run of the plan."""

    task: Task
    variant: Variant
    n: int

    @property
    def label(self) -> str:
        return f"{self.task.task_id} {self.variant.value} r{self.n}"


@dataclass
class _Outcome:
    """What one planned run gave: a result and its transcript, or an error."""

    test: TestResult | None = None
    transcript: str = ""
    error: str = ""


def _attempt(planned: _Planned, skill: Skill, agent: Agent, pool: _Judges,
             config: EvaluationConfig, run_dir: Path, folder: str) -> _Outcome:
    """One planned run, made again if the provider rate-limited its session (backoff.py).
    Never raises an Exception: whatever goes wrong costs this run only."""
    backoff, label = pool.backoff, planned.label
    for attempt in range(1, backoff.attempts + 1):
        if pool.stopping:
            return _Outcome(error=f"{label}: the evaluation was stopped before this run")
        backoff.wait()
        if pool.stopping:  # stopped during the pause, which stop() ended early
            return _Outcome(error=f"{label}: the evaluation was stopped before this run")
        try:
            test, transcript = _run_once(skill, planned.task, planned.variant, planned.n,
                                         agent, pool, config, run_dir, folder)
        # JudgeError also covers a workspace that could not be made, or Docker failing.
        # OSError: writing the log failed. Either costs this run, not the runs already made.
        except (JudgeError, JudgeUnavailable, OSError) as e:
            # The agent's provider throttled the session (HermesAgentUnavailable says so):
            # wait, with every other worker, and make the run again in a fresh workspace.
            if (getattr(e, "rate_limited", False) is True and attempt < backoff.attempts
                    and not pool.stopping):
                delay = backoff.limited()
                _log.info("metaskill: run %s was rate-limited; trying again in %.0f s",
                          label, delay)
                continue
            return _Outcome(error=clean(f"{label}: {type(e).__name__}: {e}", MAX_REASON_CHARS))
        except Exception as e:
            # Unexpected, so likely a bug of ours: it costs this run (an error, not a
            # score), never the skill's other runs or its other dimensions.
            _log.warning("metaskill: run %s of %s failed unexpectedly", label,
                         clean(skill.name, MAX_REASON_CHARS), exc_info=True)
            return _Outcome(error=clean(f"{label}: {type(e).__name__}", MAX_REASON_CHARS))
        backoff.succeeded()
        return _Outcome(test=test, transcript=transcript)
    raise AssertionError("unreachable")  # the last attempt returns


def _run_parallel(todo: list[int], workers: int, attempt: Callable[[int], _Outcome],
                  outcomes: list[_Outcome | None], stop: Callable[[], None]) -> None:
    """Run the planned runs `todo` on `workers` threads, each outcome at its index. The wait
    wakes up regularly, so Ctrl-C reaches it even on Windows, where a blocking wait on a lock
    cannot be interrupted. Interrupted, it calls `stop` (no new runs, every container
    removed, the agent's sessions stopped) before waiting for the threads, which then end
    quickly, and no thread outlives the call."""
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="metaskill-run")
    futures = {submit(executor, attempt, index): index for index in todo}
    try:
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                outcomes[futures[future]] = future.result()
            cancel.check()  # stopped: the except below stops the runs and waits for them
    except BaseException:
        stop()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    executor.shutdown(wait=True)


def _run_once(skill: Skill, task: Task, variant: Variant, n: int, agent: Agent,
              pool: _Judges, config: EvaluationConfig,
              run_dir: Path, folder: str) -> tuple[TestResult, str]:
    """One session in a fresh workspace, judged against every check of the task. Returns
    the result and the agent's transcript ("" if it gave none)."""
    workspace = pool.open(task.fixtures)
    left_out: list[str] = []
    run, failure = AgentRun(output=""), ""
    try:
        if variant is Variant.WITH_SKILL:
            # Before the clock: copying is not the agent's time.
            try:
                left_out = _put_skill(skill, workspace)
            except RunFailed as e:  # too large: it fails, rather than go unscored
                failure = str(e)
        start = time.monotonic()
        if not failure:
            try:
                run = agent.run(task.prompt,
                                skill if variant is Variant.WITH_SKILL else None, workspace)
            except Exception as e:  # the agent failing is an outcome, not a runner error
                # Unless it failed because Docker did, reached through the workspace.
                if isinstance(e, JudgeError) and not isinstance(e, RunFailed):
                    raise
                failure = f"agent failed: {type(e).__name__}: {e}"
        seconds = time.monotonic() - start

        if failure:
            verdict = _failed(failure)
        else:
            verdict = _judge(task, run, workspace, pool)
    finally:
        pool.close(workspace)

    log = f"{folder}/logs/{task.task_id}-{variant.value}-r{n}.txt"
    metrics = Metrics(
        input_tokens=run.input_tokens, output_tokens=run.output_tokens,
        reasoning_tokens=run.reasoning_tokens,
        cache_read_tokens=run.cache_read_tokens, cache_write_tokens=run.cache_write_tokens,
        llm_calls=run.llm_calls, tool_calls=run.tool_calls,
        seconds=round(_own_seconds(run) if _own_seconds(run) is not None else seconds, 3))
    if not pool.stopping:  # a run the stop killed did not fail: no log that says it did
        _write_log(run_dir / log, task, variant, n, run, metrics, verdict, left_out)
    test = TestResult(
        task_id=task.task_id,
        description=task.description,
        variant=variant,
        run=n,
        judge=JudgeType.LLM if any(c.judge is JudgeType.LLM for c in task.checks)
        else JudgeType.DETERMINISTIC,
        passed=verdict.passed,
        score=verdict.score,
        reason=verdict.reason,
        metrics=metrics,
        output=_truncate(run.output, config.output_max_chars, log),
        evidence=verdict.evidence,
    )
    return test, run.transcript


def _own_seconds(run: AgentRun) -> float | None:
    """The agent's own measure of its session, if it gave a usable one."""
    value = run.seconds
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def copied(skill: Skill, rel: Path) -> bool:
    """Whether the runner copies this bundled file into the sandbox: not a link, not a hard
    link, and inside the skill (any of those could be a file outside it, e.g.
    ~/.hermes/.env). One rule for the copy and for any list of files shown to the agent, so
    the agent is never pointed at a file that is not there."""
    path = Path(skill.path) / rel
    try:
        return (not path.is_symlink() and path.resolve().is_relative_to(Path(skill.path).resolve())
                and path.stat().st_nlink <= 1)
    except OSError:
        return False


def _put_skill(skill: Skill, workspace: Workspace) -> list[str]:
    """Copy the skill into workspace.skill_dir, laid out as on disk, and return the
    files left out, for the log. The runner does this rather than the agent, so every
    with_skill run starts from the same files.

    Links and hard links are left out, as for the judge (judge.skill_sources): either
    could be a file outside the skill, e.g. ~/.hermes/.env. If SKILL.md itself is one,
    the run is an error rather than a run without the skill.
    """
    folders: dict[PurePosixPath, list[Path]] = {}
    left_out: list[str] = []
    for rel in [Path("SKILL.md"), *skill.files]:
        path = Path(skill.path) / rel
        if not copied(skill, rel):
            # An error, not a failed run, and before the agent starts: the agent is given
            # skill.body as its prompt, so running it would send a SKILL.md that links to,
            # say, ~/.hermes/.env to the model provider. judge.skill_sources refuses it too.
            if rel == Path("SKILL.md"):
                raise JudgeError("SKILL.md is a link or hard link, so it may be a file "
                                 "outside the skill; it was not copied into the sandbox")
            left_out.append(rel.as_posix())
            continue
        folders.setdefault(PurePosixPath(rel.parent.as_posix()), []).append(path)
    for folder, paths in folders.items():
        workspace.put(paths, str(PurePosixPath(workspace.skill_dir) / folder))
    return left_out


def _judge(task: Task, run: AgentRun, workspace: Workspace, pool: _Judges) -> Verdict:
    """Every check of the task on the run. The judges read a copy of the workspace's
    folder, made once, before any check runs; `exit_code` commands run in the workspace
    itself, where the agent ran. A run that broke its workspace fails: it is not left
    out, or a skill could leave itself unscored."""
    workdir = Path(tempfile.mkdtemp(prefix=f"metaskill-{task.task_id}-"))
    try:
        workspace.export(workdir)
        output = RunOutput(text=run.output, workdir=workdir)
        pool.workspace = workspace
        return _combine(_verdicts(task, output, pool))
    except RunFailed as e:
        return _failed(str(e))
    except (JudgeError, JudgeUnavailable):
        raise  # the judge or Docker, not the run: an error, as before
    except Exception as e:
        # Anything else came from judging what the run left behind (its files, links or
        # output), so it is the run's doing: it fails rather than escaping, which would
        # drop the whole skill from the evaluation. Logged in full, as it may be our bug.
        _log.warning("metaskill: a check of task %s raised while judging a run", task.task_id,
                     exc_info=True)
        return _failed(f"the run's result could not be judged ({type(e).__name__})")
    finally:
        pool.workspace = None
        shutil.rmtree(workdir, ignore_errors=True)


def _verdicts(task: Task, output: RunOutput, pool: _Judges) -> list[Verdict]:
    """Each check's verdict, in the task's order. Two or more LLM checks are asked at once,
    each a model round-trip of its own, while the deterministic ones run here: an
    `exit_code` check must run on this thread, whose run's workspace pool.workspace is.
    An error is raised as a sequential pass would: the first check's, in order."""
    checks = task.checks
    llm = [i for i, c in enumerate(checks) if c.judge is JudgeType.LLM]
    if len(llm) < 2:
        return [pool.get(c.judge).judge(task, c, output) for c in checks]
    executor = ThreadPoolExecutor(max_workers=len(llm), thread_name_prefix="metaskill-check")
    try:
        futures = {i: submit(executor, pool.get(checks[i].judge).judge, task, checks[i], output)
                   for i in llm}
        found: dict[int, Verdict | Exception] = {}
        for i, check in enumerate(checks):
            if i not in futures:
                try:
                    found[i] = pool.get(check.judge).judge(task, check, output)
                except Exception as e:  # raised below, in check order
                    found[i] = e
        for i, future in futures.items():
            try:
                found[i] = future.result()
            except Exception as e:
                found[i] = e
    finally:
        executor.shutdown(wait=False, cancel_futures=True)  # every future is done, or raising
    verdicts = []
    for i in range(len(checks)):
        if isinstance(found[i], Exception):
            raise found[i]
        verdicts.append(found[i])
    return verdicts


def _failed(reason: str) -> Verdict:
    """The verdict of a run that failed without being judged. `reason` can hold text the
    run shaped, e.g. an exception the agent raised, so it is cleaned like a judge's."""
    return Verdict(passed=False, score=0.0, reason=clean(reason, MAX_REASON_CHARS),
                   evidence=None)


def _combine(verdicts: list[Verdict]) -> Verdict:
    """One verdict for a run: passed only if every check passed, score the mean."""
    if len(verdicts) == 1:
        return verdicts[0]
    evidence = [v.evidence for v in verdicts if v.evidence]
    return Verdict(
        passed=all(v.passed for v in verdicts),
        score=sum(v.score for v in verdicts) / len(verdicts),
        reason="; ".join(v.reason for v in verdicts),
        evidence="\n".join(evidence) or None,
    )


class _Judges:
    """The judges and sandbox for one runner call, shared by its runs, which may run on
    several threads at once (runner.md 2.7). The LLM judge is built once, and only if a task
    needs it, so its record of models and tokens covers every run. The sandbox is asked
    once whether it can run, since every run happens in it. Every workspace open now is
    known, so stop() can remove them all."""

    def __init__(self, ctx: Any, config: EvaluationConfig,
                 given: Mapping[JudgeType, TaskJudge] | None, sandbox: Sandbox,
                 backoff: Backoff | None = None):
        self.ctx, self.config, self.sandbox = ctx, config, sandbox
        self.judges: dict[JudgeType, TaskJudge] = dict(given or {})
        self.llm: Judge | None = None
        self.llm_error = ""
        self.sandbox_error: str | None = None  # None until the sandbox is asked
        # One pause for every session and LLM check of the call, after a rate limit.
        self.backoff = backoff if backoff is not None else Backoff()
        self.stopping = False
        self._lock = threading.Lock()
        self._open: dict[int, Workspace] = {}  # by id(): a workspace need not be hashable
        # The run being judged is per thread: an `exit_code` check runs in its own run's
        # workspace, whichever other runs are being judged at the same time.
        self._local = threading.local()

    @property
    def workspace(self) -> Workspace | None:
        """The workspace of the run this thread is judging, if any."""
        return getattr(self._local, "workspace", None)

    @workspace.setter
    def workspace(self, workspace: Workspace | None) -> None:
        self._local.workspace = workspace

    def run_command(self, command: str, workdir: Path,
                    timeout: float) -> tuple[int | None, str]:
        """judge.CommandRunner for `exit_code` checks: runs the command in the workspace
        of the run being judged, not in workdir, the copy the other checks read."""
        workspace = self.workspace
        if workspace is None:
            raise JudgeError("an exit_code check ran outside a run")
        result = workspace.run_command(command, timeout)
        return result.exit_code, result.stderr

    def get(self, judge_type: JudgeType) -> TaskJudge:
        with self._lock:
            if judge_type not in self.judges:
                if judge_type is JudgeType.DETERMINISTIC:
                    self.judges[judge_type] = DeterministicJudge(run_command=self.run_command)
                else:
                    built = make_judge(judge_type, ctx=self.ctx, config=self.config)
                    if isinstance(built, Judge):
                        built.backoff = self.backoff  # rate limits pause every run
                    self.judges[judge_type] = built
            judge = self.judges[judge_type]
            if isinstance(judge, Judge):
                self.llm = judge
            return judge

    def workers(self, requested: int, runs: int, host_room: Any = None) -> int:
        """Sessions to run at once: `requested` (max_parallel_runs), no more than there are
        runs, no more than the sandbox can hold (DockerSandbox.capacity), and no more than
        `host_room`, the Hermes processes the host's memory holds (host.capacity)."""
        limit = max(1, min(requested, runs))
        if isinstance(host_room, int) and not isinstance(host_room, bool) \
                and 1 <= host_room < limit:
            _log.info("metaskill: running %d sessions at once, not %d: the host's memory has "
                      "room for %d Hermes sessions at %d MB each", host_room, limit,
                      host_room, host.HERMES_MEMORY // (1024 * 1024))
            limit = host_room
        capacity = getattr(self.sandbox, "capacity", None)
        if limit > 1 and callable(capacity):
            room = capacity()
            if isinstance(room, int) and 1 <= room < limit:
                _log.info("metaskill: running %d sessions at once, not %d: the sandbox has "
                          "room for %d", room, limit, room)
                limit = room
        return limit

    def open(self, fixtures: list[Path]) -> Workspace:
        """A new workspace from the sandbox, known until close(); none once stopping."""
        if self.stopping:
            raise JudgeError("the evaluation was stopped")
        workspace = self.sandbox.open(fixtures)
        with self._lock:
            self._open[id(workspace)] = workspace
            stopped = self.stopping
        if stopped:  # stop() ran while this one was being made: it missed it
            self.close(workspace)
            raise JudgeError("the evaluation was stopped")
        return workspace

    def close(self, workspace: Workspace) -> None:
        with self._lock:
            self._open.pop(id(workspace), None)
        workspace.close()

    def halt(self) -> None:
        """No new workspace or judge call from now on. Quick: stop() calls it first."""
        with self._lock:
            self.stopping = True
        # A worker in a rate-limit pause (up to backoff.MAX_DELAY) wakes now and sees the stop,
        # and a judge call waiting on it is not made, so the threads end quickly.
        self.backoff.cancel()

    def stop(self) -> None:
        """halt(), and every open workspace removed, with whatever still runs in it.
        A run whose workspace goes ends with an error, which the caller no longer reads."""
        self.halt()
        with self._lock:
            still_open = list(self._open.values())
        for workspace in still_open:
            try:
                self.close(workspace)
            except Exception:  # one that will not close must not keep the others open
                _log.warning("metaskill: a sandbox workspace could not be removed",
                             exc_info=True)

    def unavailable(self, task: Task) -> str:
        """Why the task cannot be run in the sandbox or judged, or "" if it can."""
        if self.sandbox_error is None:
            self.sandbox_error = self.sandbox.unavailable()
        if self.sandbox_error:
            return ("SandboxUnavailable: agent runs and exit_code checks run in a sandbox, "
                    f"which cannot run: {self.sandbox_error}")
        if any(c.judge is JudgeType.LLM for c in task.checks):
            if not self.llm_error:
                try:
                    self.get(JudgeType.LLM)
                except JudgeUnavailable as e:
                    self.llm_error = f"JudgeUnavailable: {e}"
            if self.llm_error:
                return self.llm_error
        return ""


def _truncate(output: str, max_chars: int, log: str) -> str:
    """TestResult.output: the output cut to max_chars, with the path to the full log."""
    text = output if len(output) <= max_chars else output[:max_chars] + "..."
    return f"{text} (full log: {log})"


def _write_log(path: Path, task: Task, variant: Variant, n: int, run: AgentRun,
               metrics: Metrics, verdict: Verdict, left_out: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"task: {task.task_id}\nvariant: {variant.value}\nrun: {n}\n"
        f"tokens: {metrics.total_tokens} (input {metrics.reproducible_input}, "
        f"output {metrics.output_tokens}, reasoning {metrics.reasoning_tokens})\n"
        f"llm_calls: {metrics.llm_calls}\ntool_calls: {metrics.tool_calls}\n"
        f"seconds: {metrics.seconds}\n"
        # Links in the skill are not copied; a run that needed one fails without them.
        + (f"skill files left out (links): {', '.join(left_out)}\n" if left_out else "")
        + f"passed: {verdict.passed}\nscore: {verdict.score}\nreason: {verdict.reason}\n"
        f"\n--- prompt ---\n{task.prompt}\n"
        f"\n--- output ---\n{run.output}\n"
        + (f"\n--- transcript ---\n{run.transcript}\n" if run.transcript else ""),
        # A lone surrogate, e.g. from a cut emoji, cannot be encoded. Written as \ud83d, so
        # one in the output does not stop the runs left to make.
        encoding="utf-8", errors="backslashreplace",
    )
