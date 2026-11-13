"""Task runner: runs each task n times with and without the skill (docs/evaluate.md 4).

The only module that executes the agent. For every task, variant (`with_skill`,
`baseline`) and run 1..n it gives the agent a fresh sandboxed workspace with the task's
fixtures, judges the result with judge.py, writes the full log to the report folder and
records a TestResult. Quality, robustness and efficiency are computed from those results.

How an agent session is started is behind `Agent`, so tests use a fake one and no
Hermes internals are imported here. Everything the agent runs, and the command of every
`exit_code` check, runs in the run's `Workspace` (sandbox.py), never on this machine.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

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
    transcript: str = ""  # the full session for the log, if the agent provides one


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
    # Runs that could not be judged (judge unavailable or failed, or a broken check).
    # They are not in `tests`, so a missing judge does not count as a failed task.
    errors: list[str] = field(default_factory=list)
    judge_model: str = ""  # for ReportMeta.judge_model; empty if no LLM check ran
    judge_usage: TokenUsage = field(default_factory=TokenUsage)


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
              sandbox: Sandbox | None = None) -> RunnerResult:
    """Run every task `config.runs_per_task` times per variant and judge each run.

    `ctx` is the Hermes plugin context, needed only for LLM checks. `sandbox` opens the
    workspace each run happens in, and runs its `exit_code` commands; None means a
    `DockerSandbox` whose run folders are `config.sandbox_disk` in size. `judges`
    replaces the judges built from these, for tests. Logs go to `run_dir/[skill]/logs/`.

    Raises ValueError if the skill's name cannot be a folder name: it comes from the
    skill's own frontmatter, so `../x` or an absolute path would put the logs elsewhere.
    """
    if not isinstance(skill.name, str) or not SAFE_NAME.fullmatch(skill.name):
        raise ValueError(f"skill name {skill.name!r} may only contain letters, digits, "
                         "'_', '.' and '-'")
    pool = _Judges(ctx, config, judges, sandbox or DockerSandbox(disk=config.sandbox_disk))
    result = RunnerResult(tests=[])
    log_dir = run_dir / skill.name / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    for task in tasks:
        for variant in VARIANTS:
            for n in range(1, config.runs_per_task + 1):
                label = f"{task.task_id} {variant.value} r{n}"
                unavailable = pool.unavailable(task)
                if unavailable:
                    # Not worth an agent session that cannot be judged.
                    result.errors.append(clean(f"{label}: {unavailable}", MAX_REASON_CHARS))
                    continue
                try:
                    result.tests.append(
                        _run_once(skill, task, variant, n, agent, pool, config, run_dir))
                # JudgeError also covers a workspace that could not be made, or Docker
                # failing. OSError: writing the log failed. Either costs this run, not
                # the runs already made.
                except (JudgeError, JudgeUnavailable, OSError) as e:
                    result.errors.append(
                        clean(f"{label}: {type(e).__name__}: {e}", MAX_REASON_CHARS))

    if pool.llm is not None:
        result.judge_model = pool.llm.judge_model
        result.judge_usage = pool.llm.total_usage
    return result


def _run_once(skill: Skill, task: Task, variant: Variant, n: int, agent: Agent,
              pool: _Judges, config: EvaluationConfig, run_dir: Path) -> TestResult:
    """One session in a fresh workspace, judged against every check of the task."""
    workspace = pool.sandbox.open(task.fixtures)
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
        workspace.close()

    log = f"{skill.name}/logs/{task.task_id}-{variant.value}-r{n}.txt"
    metrics = Metrics(
        input_tokens=run.input_tokens, output_tokens=run.output_tokens,
        reasoning_tokens=run.reasoning_tokens,
        cache_read_tokens=run.cache_read_tokens, cache_write_tokens=run.cache_write_tokens,
        llm_calls=run.llm_calls, tool_calls=run.tool_calls, seconds=round(seconds, 3))
    _write_log(run_dir / log, task, variant, n, run, metrics, verdict, left_out)
    return TestResult(
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


def _put_skill(skill: Skill, workspace: Workspace) -> list[str]:
    """Copy the skill into workspace.skill_dir, laid out as on disk, and return the
    files left out, for the log. The runner does this rather than the agent, so every
    with_skill run starts from the same files.

    Links and hard links are left out, as for the judge (judge.skill_sources): either
    could be a file outside the skill, e.g. ~/.hermes/.env. If SKILL.md itself is one,
    the run is an error rather than a run without the skill.
    """
    root = Path(skill.path).resolve()
    folders: dict[PurePosixPath, list[Path]] = {}
    left_out: list[str] = []
    for rel in [Path("SKILL.md"), *skill.files]:
        path = Path(skill.path) / rel
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or path.stat().st_nlink > 1):
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
        return _combine([pool.get(c.judge).judge(task, c, output) for c in task.checks])
    except RunFailed as e:
        return _failed(str(e))
    finally:
        pool.workspace = None
        shutil.rmtree(workdir, ignore_errors=True)


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
    """The judges for one runner call. The LLM judge is built once, and only if a task
    needs it, so its record of models and tokens covers every run. The sandbox is asked
    once whether it can run, since every run happens in it."""

    def __init__(self, ctx: Any, config: EvaluationConfig,
                 given: Mapping[JudgeType, TaskJudge] | None, sandbox: Sandbox):
        self.ctx, self.config, self.sandbox = ctx, config, sandbox
        self.judges: dict[JudgeType, TaskJudge] = dict(given or {})
        self.llm: Judge | None = None
        self.llm_error = ""
        self.sandbox_error: str | None = None  # None until the sandbox is asked
        self.workspace: Workspace | None = None  # the run being judged

    def run_command(self, command: str, workdir: Path,
                    timeout: float) -> tuple[int | None, str]:
        """judge.CommandRunner for `exit_code` checks: runs the command in the workspace
        of the run being judged, not in workdir, the copy the other checks read."""
        if self.workspace is None:
            raise JudgeError("an exit_code check ran outside a run")
        result = self.workspace.run_command(command, timeout)
        return result.exit_code, result.stderr

    def get(self, judge_type: JudgeType) -> TaskJudge:
        if judge_type not in self.judges:
            if judge_type is JudgeType.DETERMINISTIC:
                self.judges[judge_type] = DeterministicJudge(run_command=self.run_command)
            else:
                self.judges[judge_type] = make_judge(judge_type, ctx=self.ctx,
                                                     config=self.config)
        judge = self.judges[judge_type]
        if isinstance(judge, Judge):
            self.llm = judge
        return judge

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
