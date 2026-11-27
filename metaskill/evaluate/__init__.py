"""Part 2: scoring and ranking skills (docs/evaluate.md).

evaluate_skill and rank_skills are the public API, called by the plugin's `metaskill`
tool. Each call scores its skills fresh, ranks them and writes one report folder
(evaluate.md section 2). They return what the agent may see: the table, report paths and
counts, never quotes or finding text, which stay in the reports.

A dimension whose module is not built yet is `skipped`, with a score of None, so its
skill is not ranked (evaluate.md 5.2).

With a task file (tasks_path), each skill's tasks run once in the sandbox with a Hermes
agent (runner.py, hermes_agent.py), and every task-based dimension scores those same runs
(score_runs). Without one, no task runs: no agent session, Docker is not asked for, and
the task-based dimensions are skipped. `lift` comes from Quality's assessment of the same
runs (quality.assess_runs); None without tasks, or when Quality cannot compare.

One LLM judge is built per call and shared by the dimensions in JUDGED (Clarity). The
report's `judge_model` and `judge_usage` cover it, the judge of the task runs' LLM checks,
and any dimension that builds a judge of its own and reports it on its DimensionScore (the
safety review's deep tier).

A judge whose provider fails mid-run (a rate limit, an outage) makes the dimension `error`
for every skill after that point; the result's `warnings` say the provider was the cause.

The plugin imports judge.py through this package when it registers, so whatever is
imported at the top here has to load with it. `safety` is imported where it is used: a
failure there must cost `evaluate`, not the whole plugin.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .judge import (
    JUDGE_TASK, MAX_REASON_CHARS, Judge, JudgeCallFailed, JudgeError, JudgeUnavailable,
    TokenUsage, clean)
from .models import (
    Dimension,
    DimensionScore,
    EvaluationConfig,
    Report,
    Skill,
    SkillResult,
    Status,
)
# Under another name: `rank` here would hide the rank module.
from .rank import Ranking, make_scores, markdown, table
from .rank import rank as rank_results
from .runner import RunnerResult, report_dir, run_tasks, timestamp
from .tasks import TaskError, load_tasks

_log = logging.getLogger(__name__)

# The module that scores each dimension (evaluate.md section 6), once it exists. Each has
# score(skill, config, llm=...) -> DimensionScore, so adding one needs no change here.
SCORERS = {
    Dimension.QUALITY: "quality",
    Dimension.ROBUSTNESS: "robustness",
    Dimension.CLARITY: "clarity",
    Dimension.CONFIG_CLEANLINESS: "config_cleanliness",
    Dimension.EFFICIENCY: "efficiency",
    Dimension.SAFETY: "safety",
}
# Dimensions scored from task runs. Their modules have score_runs(skill, config, runs) ->
# DimensionScore, given the skill's RunnerResult; the others have score() above.
TASK_DIMENSIONS = frozenset({Dimension.QUALITY, Dimension.ROBUSTNESS, Dimension.EFFICIENCY})

# Dimensions whose score() also takes judge=, the run's shared Judge.
JUDGED = {Dimension.CLARITY}

# Dimensions whose score() takes task=, the auxiliary task their own model calls go through
# (safety's deep tier: the review's judge and Cisco's LLM analyzer). Given only where Hermes
# registered it, as Judge.from_context does, so every judge of a call runs on the model the
# user set for "Metaskill judge", not the deep tier alone on the main model.
TASKED = {Dimension.SAFETY}

# DimensionStatus.detail when the judge's provider did not answer (JudgeCallFailed).
PROVIDER_FAILED = "failed: the judge model's provider did not answer"


def evaluate_skill(skill_path: str | Path, *, config_path: str | Path | None = None,
                   output_path: str | Path | None = None, tasks_path: str | Path | None = None,
                   ctx: Any = None, agent: Any = None, sandbox: Any = None,
                   judges: Any = None) -> dict[str, Any]:
    """Score one skill, a folder or its SKILL.md, and write its report. With tasks_path,
    its tasks run first (real model calls)."""
    return _evaluate([skill_path], config_path, output_path, tasks_path, ctx, agent, sandbox, judges)


def rank_skills(skill_paths: list[str | Path], *, config_path: str | Path | None = None,
                output_path: str | Path | None = None, tasks_path: str | Path | None = None,
                ctx: Any = None, agent: Any = None, sandbox: Any = None,
                judges: Any = None) -> dict[str, Any]:
    """Score skills in one run, rank them and write their report. With tasks_path, every
    skill runs the same tasks. A skill that cannot be loaded is listed in `warnings`; the
    others go on."""
    return _evaluate(skill_paths, config_path, output_path, tasks_path, ctx, agent, sandbox, judges)


def _evaluate(paths: list[str | Path], config_path: str | Path | None,
              output_path: str | Path | None, tasks_path: str | Path | None,
              ctx: Any, agent: Any = None, sandbox: Any = None,
              judges: Any = None) -> dict[str, Any]:
    from . import safety

    try:
        config = _config(config_path, output_path, tasks_path)
    except (OSError, ValueError, TypeError, AttributeError):
        # Not the exception's message: it may echo the file (tools.py).
        return {"status": "error", "error": "config_path is not a valid evaluation config"}
    # A relative folder means the user's session, not the gateway's own folder (tools.py).
    session_cwd = getattr(ctx, "session_cwd", None)
    if callable(session_cwd):
        config.output_dir = Path(session_cwd()) / config.output_dir
        if config.tasks_path is not None:
            config.tasks_path = Path(session_cwd()) / config.tasks_path

    tasks = None
    if config.tasks_path is None:
        # Nothing runs, so only what safety needs; Docker is not asked for.
        missing = safety.missing_dependencies()
    else:
        try:
            tasks = load_tasks(config.tasks_path)
        except TaskError as e:  # names the file and the task: the user's own, not a skill's
            return {"status": "error",
                    "error": clean(f"tasks_path is not a usable task file: {e}", MAX_REASON_CHARS)}
        inside = _task_files_in_skills(config.tasks_path, tasks, paths)
        if inside:  # the with-skill agent would read the checks and expected answers
            return {"status": "error",
                    "error": clean(f"the task file must be outside every evaluated skill: "
                                   f"{inside} is inside one, so the skill's copy in the sandbox "
                                   f"would hold the answers; nothing was run", MAX_REASON_CHARS)}
        from . import dependencies

        sandbox = sandbox if sandbox is not None else _make_sandbox(config)
        missing = dependencies.missing_dependencies(sandbox)
        if any(d.required for d in missing):  # dependencies.md: nothing starts without them
            return {"status": "error",
                    "error": "running the tasks needs what `dependencies` lists; nothing was run",
                    "dependencies": [asdict(d) for d in missing]}
        agent = agent if agent is not None else _make_agent(config)
        problem = _check_agent(agent, sandbox)
        if problem:  # one clear error now, not one per run
            return {"status": "error",
                    "error": clean(f"the task agent (Hermes) could not be set up, so nothing was "
                                   f"run: {problem}", MAX_REASON_CHARS),
                    "dependencies": [asdict(d) for d in missing]}

    # With tasks, before scoring: the runs write their logs into this call's folder.
    created = not config.output_dir.exists()
    run_dir, started = _new_run_dir(config) if tasks is not None else (None, "")
    llm = getattr(ctx, "llm", None)
    judge = _judge(ctx, config)
    results: list[SkillResult] = []
    warnings: list[str] = []
    sessions = run_errors = 0
    judge_models: set[str] = set()
    judge_usage = TokenUsage()
    names: set[str] = set()  # names of the skills so far
    shared: set[str] = set()  # names already warned about
    for number, path in enumerate(paths, 1):
        try:
            skill = Skill.load(_folder(path))
            log_folder = _log_folder(number)
            if skill.name in names and skill.name not in shared:
                shared.add(skill.name)
                where = ("" if tasks is None else
                         ", and each one's Logs line says which folder holds its runs")
                warnings.append(clean(
                    f"more than one skill is named {skill.name}: the report has a row for "
                    f"each{where}", MAX_REASON_CHARS))
            names.add(skill.name)
            result, runs, judged = _result(skill, config, llm, tasks, judge=judge,
                                           agent=agent, sandbox=sandbox, run_dir=run_dir, ctx=ctx, judges=judges,
                                           log_folder=log_folder)
            if tasks is not None:  # without task runs there are no logs to point to
                result.log_folder = log_folder
        except Exception as e:  # one bad skill must not stop the others
            warnings.append(clean(f"{path} could not be evaluated: {type(e).__name__}",
                                  MAX_REASON_CHARS))
            continue
        results.append(result)
        errors = runs.errors if runs else []
        if runs and runs.judge_model:  # task checks an LLM judged (runner.md 2.3)
            judge_models.update(m for m in runs.judge_model.split(", ") if m)
            judge_usage = judge_usage + runs.judge_usage
        for model, usage in judged:  # dimensions an LLM judged, e.g. the deep safety tier
            judge_models.update(m for m in model.split(", ") if m)
            judge_usage = judge_usage + TokenUsage(usage.get("input_tokens", 0),
                                                   usage.get("output_tokens", 0),
                                                   usage.get("calls", 0))
        sessions += len(result.tests)
        run_errors += len(errors)
        if errors:  # infrastructure, not the skill: the runs are left out, and it says so
            # The kind only: the message can hold this machine's paths (a stderr tail),
            # which stay in the report (the task-based dimensions' detail).
            warnings.append(clean(f"{result.name}: {len(errors)} task run(s) could not be made "
                                  f"({_error_kind(errors[0])}); the report has the details",
                                  MAX_REASON_CHARS))
    if not results:
        _remove_if_empty(run_dir, config.output_dir if created else None)
        return {"status": "error", "error": "no skill could be evaluated",
                "warnings": warnings}
    warnings += _provider_warnings(results)

    if run_dir is None:
        run_dir, started = _new_run_dir(config)
    ranking = rank_results(results, config)
    versions = safety.versions()
    versions["clarity_rubric_version"] = _clarity_rubric_version()
    meta = config.to_meta(started, [str(r.name) for r in results], **versions)
    # to_meta gives the model asked for; this field says which models actually judged
    # (models.md): the judge of the task runs' LLM checks, of any dimension that built its
    # own judge (the deep safety tier, through DimensionScore.judge_*), and the run's shared
    # judge (Clarity). The shared judge was built once for this call, so its totals are
    # exactly this call's, and no dimension that uses it reports them as well.
    if judge is not None and judge.total_usage.calls:
        judge_models.update(m for m in judge.judge_model.split(", ") if m)
        judge_usage = judge_usage + judge.total_usage
    meta.judge_model = ", ".join(sorted(judge_models))
    if judge_usage.calls:
        meta.judge_usage = asdict(judge_usage)
    if agent is not None:  # the model(s) the sessions actually ran on
        meta.agent_model = ", ".join(sorted(getattr(agent, "models_used", ())))
    artifacts = _write(Report(meta, [e.result for e in ranking.entries]), ranking, run_dir)
    response = {
        "artifacts": artifacts,
        "table": table(ranking),
        "counts": {"skills": len(results), "ranked": len(ranking.ranked),
                   "unranked": len(ranking.unranked)},
        "warnings": warnings,
        "partial": _partial(ranking),
        "dependencies": [asdict(d) for d in missing],
    }
    if tasks is not None:
        response["task_runs"] = {"tasks": len(tasks), "sessions": sessions, "errors": run_errors}
    return response


def _log_folder(number: int) -> str:
    """The run log folder of the `number`th skill of a call, counted from 1: `skill-01`.

    Never the skill's name, which comes from the skill's own frontmatter and could be a
    report file (`ranked-evaluation.json`), a Windows device (`NUL`), a path (`../x`) or
    the same folder as another skill's (`foo` and `foo.` on Windows)."""
    return f"skill-{number:02d}"


def _config(config_path: str | Path | None, output_path: str | Path | None,
            tasks_path: str | Path | None = None) -> EvaluationConfig:
    config = EvaluationConfig()
    if config_path:
        data = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
        config = EvaluationConfig.from_dict(data)
    if output_path:
        config.output_dir = Path(output_path)
    if tasks_path:
        config.tasks_path = Path(tasks_path)
    return config


def _error_kind(error: str) -> str:
    """The exception type in a runner error, `<task> <variant> r<n>: <Type>: <message>`."""
    parts = error.split(": ", 2)
    return parts[1] if len(parts) == 3 and parts[1].isidentifier() else "see the report"


def _check_agent(agent: Any, sandbox: Any) -> str:
    """Before any run: set the agent up once in a fresh workspace, without a model (for
    Hermes, HermesAgent.check). Returns what is wrong, or "" if it works. A problem here
    would otherwise fail every run separately. Agents without a check are taken as ready."""
    check = getattr(agent, "check", None)
    if not callable(check):
        return ""
    from .hermes_agent import HermesAgentUnavailable

    try:
        workspace = sandbox.open([])
    except JudgeError as e:  # Docker failing to give a workspace
        return f"the sandbox could not open a workspace ({type(e).__name__})"
    try:
        check(workspace)
    except HermesAgentUnavailable as e:  # its message holds no stderr (hermes_agent.py)
        return str(e)
    except (JudgeError, TimeoutError) as e:  # the sandbox failing, or Hermes hanging
        return f"the agent check failed ({type(e).__name__})"
    finally:
        workspace.close()
    return ""


def _remove_if_empty(*folders: Path | None) -> None:
    """Remove folders this call made that nothing was written to, innermost first; a
    folder holding run logs stays."""
    for folder in folders:
        if folder is not None:
            try:
                folder.rmdir()
            except OSError:
                pass


def _make_sandbox(config: EvaluationConfig) -> Any:
    """The sandbox the task runs use; replaced in tests."""
    from .sandbox import DockerSandbox

    return DockerSandbox(disk=config.sandbox_disk)


def _make_agent(config: EvaluationConfig) -> Any:
    """The agent the task runs use: a Hermes session (hermes_agent.py); replaced in tests."""
    from .hermes_agent import HermesAgent

    return HermesAgent(model=config.agent_model or None, provider=config.agent_provider or None,
                       max_iterations=config.agent_max_iterations, timeout=config.agent_timeout)


def _task_files_in_skills(tasks_path: Path, tasks: list, skill_paths: list[str | Path]) -> str:
    """The first task file or fixture that lies inside one of the skills' folders, or "".
    The runner copies a skill's folder into the sandbox, so a task file there would hand the
    with-skill agent the checks and the expected answers, and the baseline would not have them."""
    try:
        folders = [_folder(p).resolve() for p in skill_paths]
        files = [tasks_path.resolve()] + [f.resolve() for t in tasks for f in t.fixtures]
    except (OSError, RuntimeError):
        return ""
    for f in files:
        if any(f == d or d in f.parents for d in folders):
            return f.name
    return ""


def _folder(path: str | Path) -> Path:
    """The skill's folder: the tool takes a folder or the SKILL.md in it."""
    path = Path(path)
    return path.parent if path.name.lower() == "skill.md" else path


def _clarity_rubric_version() -> str:
    """clarity.RUBRIC_VERSION, or "" if clarity.py does not import. Clarity is then already
    `error` for every skill (_score), and the rest of the report must still be written."""
    try:
        from . import clarity
    except Exception:
        _log.warning("metaskill: clarity could not be imported", exc_info=True)
        return ""
    return clarity.RUBRIC_VERSION


def _judge(ctx: Any, config: EvaluationConfig) -> Judge | None:
    """The run's LLM judge, or None when Hermes gives no model access. A missing provider
    only shows up on the first call, as JudgeUnavailable from the dimension (judge.md 3)."""
    try:
        return Judge.from_context(ctx, config)
    except JudgeUnavailable:
        return None


def _result(skill: Skill, config: EvaluationConfig, llm: Any, tasks: list | None = None, *,
            judge: Judge | None = None, agent: Any = None, sandbox: Any = None,
            run_dir: Path | None = None, ctx: Any = None, log_folder: str | None = None, judges: Any = None,
            ) -> tuple[SkillResult, RunnerResult | None, list[tuple[str, dict[str, int]]]]:
    """The skill's result, its task runs (None when no tasks ran), and the (judge_model,
    judge_usage) of every dimension that judged with a judge of its own. judge is the
    run's shared Judge, for the dimensions in JUDGED; evaluate totals its use itself."""
    from . import safety

    runs = None
    if tasks is not None:
        try:
            runs = run_tasks(skill, tasks, agent, config=config, run_dir=run_dir, ctx=ctx,
                             sandbox=sandbox, log_folder=log_folder, judges=judges)
        except (ValueError, OSError) as e:
            # The log folder could not be made (a full disk, a permission): no run, but the
            # skill keeps its row and every dimension that needs no run.
            runs = RunnerResult(tests=[], task_ids=[t.task_id for t in tasks],
                                errors=[clean(f"task runs: {type(e).__name__}: {e}",
                                              MAX_REASON_CHARS)])
    task = JUDGE_TASK if hasattr(ctx, "register_auxiliary_task") else None
    found = {d: _score(d, skill, config, llm, runs, judge, task) for d in Dimension}
    return SkillResult(
        name=skill.name,
        scores=make_scores(found, config),
        lift=_lift(config, runs),
        dimensions={d: score.to_status() for d, score in found.items()},
        tests=runs.tests if runs else [],
        rubric=[r for score in found.values() for r in score.rubric],
        findings=[f for score in found.values() for f in score.findings],
        local_warnings=safety.local_warnings(skill),
    ), runs, [(s.judge_model, s.judge_usage) for s in found.values() if s.judge_usage]


def _lift(config: EvaluationConfig, runs: RunnerResult | None) -> float | None:
    """SkillResult.lift from Quality, which defines it (evaluate.md 4.1): with-skill minus
    baseline pass rate over the skill's runs. None when no tasks ran, or when Quality cannot
    compare (incomplete evidence, runner errors), never an invented 0."""
    if runs is None:
        return None
    try:
        from . import quality

        return quality.assess_runs(config, runs).lift
    except Exception:  # Quality's own score reports what went wrong
        _log.warning("metaskill: lift could not be computed", exc_info=True)
        return None


def _score(dimension: Dimension, skill: Skill, config: EvaluationConfig,
           llm: Any, runs: RunnerResult | None = None, judge: Judge | None = None,
           task: str | None = None) -> DimensionScore:
    """The dimension's score from its module. Not built is `skipped`. A module that is
    there but fails is `error`, as a broken import must not pass for an unbuilt one. A
    task-based dimension gets the skill's runs, and is `skipped` when no tasks ran."""
    module = SCORERS[dimension]
    name = f"{__name__}.{module}"
    if importlib.util.find_spec(name) is None:
        return DimensionScore(dimension, None, Status.SKIPPED, reason="Not scored",
                              detail="not built yet")
    try:
        # Imported first even when no tasks ran, so a broken module is still an error.
        scorer = importlib.import_module(name)
        if dimension in TASK_DIMENSIONS:
            if runs is None:
                return DimensionScore(dimension, None, Status.SKIPPED, reason="Not scored",
                                      detail=f"{dimension.value} needs task runs; no tasks "
                                             "were given (tasks_path)")
            return scorer.score_runs(skill, config, runs)
        extra: dict[str, Any] = {"judge": judge} if dimension in JUDGED else {}
        if dimension in TASKED and task:
            extra["task"] = task
        return scorer.score(skill, config, llm=llm, **extra)
    except JudgeUnavailable:  # judge.md 3
        return DimensionScore(dimension, None, Status.SKIPPED, reason="Not scored",
                              detail="no judge model is available")
    except Exception as e:
        # The report keeps only the type; the full error goes to the host's log. The name
        # is the skill's own frontmatter, so it is cleaned: a newline could fake a log line.
        _log.warning("metaskill: %s failed for %s", dimension.value,
                     clean(skill.name, MAX_REASON_CHARS), exc_info=True)
        detail = (PROVIDER_FAILED if isinstance(e, JudgeCallFailed)
                  else f"failed: {type(e).__name__}")
        return DimensionScore(dimension, None, Status.ERROR, reason="Not scored",
                              detail=detail)


def _provider_warnings(results: list[SkillResult]) -> list[str]:
    """One warning per dimension the judge's provider failed for, so skills left unranked
    by a rate limit or an outage are not read as broken skills."""
    warnings = []
    for dimension in Dimension:
        failed = sum(1 for r in results
                     if r.dimensions[dimension].detail == PROVIDER_FAILED)
        if failed:
            warnings.append(
                f"The judge model's provider did not answer for {failed} of {len(results)} "
                f"skill(s) (e.g. a rate limit or an outage), so their {dimension.value} is "
                "not scored and they are unranked. Run them again later.")
    return warnings


def _new_run_dir(config: EvaluationConfig) -> tuple[Path, str]:
    """A new folder for this call, and its timestamp. Reports are never overwritten, so
    a call in the same second as another takes the next free one."""
    now = datetime.now()
    while True:
        run_dir = report_dir(config, now)
        try:
            run_dir.mkdir(parents=True)
            return run_dir, timestamp(now)
        except FileExistsError:
            now += timedelta(seconds=1)


def _write(report: Report, ranking: Ranking, run_dir: Path) -> list[str]:
    """The two report files (evaluate.md section 2), and their paths."""
    data = run_dir / "ranked-evaluation.json"
    data.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    readable = run_dir / "ranked-evaluation.md"
    readable.write_text(markdown(report.meta, ranking), encoding="utf-8")
    return [str(data), str(readable)]


def _partial(ranking: Ranking) -> list[str]:
    """Says so when the result is partial, so nobody reads a safety score as a ranking."""
    missing = [d.value for d in Dimension if any(d in e.missing for e in ranking.unranked)]
    if not missing:
        return []
    return [f"Partial result: {len(ranking.unranked)} skill(s) not ranked, with no score "
            f"for {', '.join(missing)}. The notes say why."]
