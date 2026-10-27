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
from pathlib import Path
from typing import Any, Protocol

from .judge import (
    DeterministicJudge,
    Judge,
    JudgeError,
    JudgeUnavailable,
    RunOutput,
    TaskJudge,
    TokenUsage,
    Verdict,
    make_judge,
)
from .models import (
    EvaluationConfig, JudgeType, Metrics, Skill, Task, TestResult, Variant)
from .sandbox import DockerSandbox, Sandbox, Workspace
from .tasks import SAFE_NAME, load_tasks

VARIANTS = (Variant.WITH_SKILL, Variant.BASELINE)


@dataclass
class AgentRun:
    """What one agent session returned."""

    output: str  # the agent's final reply
    tokens: int = 0
    tool_calls: int = 0
    transcript: str = ""  # the full session for the log, if the agent provides one


class Agent(Protocol):
    """Starts one agent session. `skill` is None for the baseline run.

    The session itself (its model calls) runs on this machine, but every command it
    runs and every file it reads or writes goes through `workspace`, the run's sandbox,
    whose `root` is its working folder. Skill files it needs there, such as bundled
    scripts, it copies into `workspace.skill_dir` with `workspace.put`. The Hermes agent
    does this with a terminal and file backend of its own that calls the workspace, not
    with Hermes' Docker backend, so every isolation flag stays in sandbox.py.
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
                    result.errors.append(f"{label}: {unavailable}")
                    continue
                try:
                    result.tests.append(
                        _run_once(skill, task, variant, n, agent, pool, config, run_dir))
                # JudgeError also covers a workspace that could not be made or copied
                # out. OSError: writing the log failed. Either costs this run, not the
                # runs already made.
                except (JudgeError, JudgeUnavailable, OSError) as e:
                    result.errors.append(f"{label}: {type(e).__name__}: {e}")

    if pool.llm is not None:
        result.judge_model = pool.llm.judge_model
        result.judge_usage = pool.llm.total_usage
    return result


def _run_once(skill: Skill, task: Task, variant: Variant, n: int, agent: Agent,
              pool: _Judges, config: EvaluationConfig, run_dir: Path) -> TestResult:
    """One session in a fresh workspace, judged against every check of the task."""
    workspace = pool.sandbox.open(task.fixtures)
    try:
        start = time.monotonic()
        try:
            run = agent.run(task.prompt, skill if variant is Variant.WITH_SKILL else None,
                            workspace)
            failure = ""
        except Exception as e:  # the agent failing is an outcome, not a runner error
            run, failure = AgentRun(output=""), f"agent failed: {type(e).__name__}: {e}"
        seconds = time.monotonic() - start

        if failure:
            verdict = Verdict(passed=False, score=0.0, reason=failure, evidence=None)
        else:
            verdict = _judge(task, run, workspace, pool)
    finally:
        workspace.close()

    log = f"{skill.name}/logs/{task.task_id}-{variant.value}-r{n}.txt"
    metrics = Metrics(tokens=run.tokens, seconds=round(seconds, 3), tool_calls=run.tool_calls)
    _write_log(run_dir / log, task, variant, n, run, metrics, verdict)
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


def _judge(task: Task, run: AgentRun, workspace: Workspace, pool: _Judges) -> Verdict:
    """Every check of the task on the run. The judges read a copy of the workspace's
    folder, made once, before any check runs; `exit_code` commands run in the workspace
    itself, where the agent ran."""
    workdir = Path(tempfile.mkdtemp(prefix=f"metaskill-{task.task_id}-"))
    try:
        workspace.export(workdir)
        output = RunOutput(text=run.output, workdir=workdir)
        pool.workspace = workspace
        return _combine([pool.get(c.judge).judge(task, c, output) for c in task.checks])
    finally:
        pool.workspace = None
        shutil.rmtree(workdir, ignore_errors=True)


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
               metrics: Metrics, verdict: Verdict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"task: {task.task_id}\nvariant: {variant.value}\nrun: {n}\n"
        f"tokens: {metrics.tokens}\nseconds: {metrics.seconds}\n"
        f"tool_calls: {metrics.tool_calls}\n"
        f"passed: {verdict.passed}\nscore: {verdict.score}\nreason: {verdict.reason}\n"
        f"\n--- prompt ---\n{task.prompt}\n"
        f"\n--- output ---\n{run.output}\n"
        + (f"\n--- transcript ---\n{run.transcript}\n" if run.transcript else ""),
        encoding="utf-8",
    )
