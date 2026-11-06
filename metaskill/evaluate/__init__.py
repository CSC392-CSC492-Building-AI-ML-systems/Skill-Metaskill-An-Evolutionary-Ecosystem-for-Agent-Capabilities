"""Part 2: scoring and ranking skills (docs/evaluate.md).

evaluate_skill and rank_skills are the public API, called by the plugin's `metaskill`
tool. Each call scores its skills fresh, ranks them and writes one report folder
(evaluate.md section 2). They return what the agent may see: the table, report paths and
counts, never quotes or finding text, which stay in the reports.

A dimension whose module is not built yet is `skipped`, with a score of None, so its
skill is not ranked (evaluate.md 5.2). No built dimension runs tasks yet, so nothing here
needs the sandbox and `lift` is None.

The plugin imports judge.py through this package when it registers, so whatever is
imported at the top here has to load with it. `safety` is imported where it is used: a
failure there must cost `evaluate`, not the whole plugin.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .judge import MAX_REASON_CHARS, JudgeUnavailable, clean
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
from .runner import report_dir, timestamp

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


def evaluate_skill(skill_path: str | Path, *, config_path: str | Path | None = None,
                   output_path: str | Path | None = None, ctx: Any = None) -> dict[str, Any]:
    """Score one skill, a folder or its SKILL.md, and write its report."""
    return _evaluate([skill_path], config_path, output_path, ctx)


def rank_skills(skill_paths: list[str | Path], *, config_path: str | Path | None = None,
                output_path: str | Path | None = None, ctx: Any = None) -> dict[str, Any]:
    """Score skills in one run, rank them and write their report. A skill that cannot be
    loaded is listed in `warnings`; the others go on."""
    return _evaluate(skill_paths, config_path, output_path, ctx)


def _evaluate(paths: list[str | Path], config_path: str | Path | None,
              output_path: str | Path | None, ctx: Any) -> dict[str, Any]:
    from . import safety

    try:
        config = _config(config_path, output_path)
    except (OSError, ValueError, TypeError, AttributeError):
        # Not the exception's message: it may echo the file (tools.py).
        return {"status": "error", "error": "config_path is not a valid evaluation config"}

    # Only what safety needs: no built dimension runs tasks, so Docker is not asked for.
    # Once one does, this is dependencies.missing_dependencies(), and evaluate calls
    # require_dependencies() before it starts (dependencies.md).
    missing = safety.missing_dependencies()
    llm = getattr(ctx, "llm", None)
    results: list[SkillResult] = []
    warnings: list[str] = []
    for path in paths:
        try:
            results.append(_result(Skill.load(_folder(path)), config, llm))
        except Exception as e:  # one bad skill must not stop the others
            warnings.append(clean(f"{path} could not be evaluated: {type(e).__name__}",
                                  MAX_REASON_CHARS))
    if not results:
        return {"status": "error", "error": "no skill could be evaluated",
                "warnings": warnings}

    ranking = rank_results(results, config)
    run_dir, started = _new_run_dir(config)
    versions = safety.versions()
    meta = config.to_meta(started, [str(r.name) for r in results], **versions)
    # No judge has run: to_meta gives the model asked for, and this field says which
    # models judged (models.md).
    meta.judge_model = ""
    artifacts = _write(Report(meta, [e.result for e in ranking.entries]), ranking, run_dir)
    return {
        "artifacts": artifacts,
        "table": table(ranking),
        "counts": {"skills": len(results), "ranked": len(ranking.ranked),
                   "unranked": len(ranking.unranked)},
        "warnings": warnings + _partial(ranking),
        "dependencies": [asdict(d) for d in missing],
    }


def _config(config_path: str | Path | None,
            output_path: str | Path | None) -> EvaluationConfig:
    config = EvaluationConfig()
    if config_path:
        data = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
        config = EvaluationConfig.from_dict(data)
    if output_path:
        config.output_dir = Path(output_path)
    return config


def _folder(path: str | Path) -> Path:
    """The skill's folder: the tool takes a folder or the SKILL.md in it."""
    path = Path(path)
    return path.parent if path.name.lower() == "skill.md" else path


def _result(skill: Skill, config: EvaluationConfig, llm: Any) -> SkillResult:
    from . import safety

    found = {d: _score(d, skill, config, llm) for d in Dimension}
    return SkillResult(
        name=skill.name,
        scores=make_scores(found, config),
        lift=None,  # no tasks ran
        dimensions={d: score.to_status() for d, score in found.items()},
        rubric=[r for score in found.values() for r in score.rubric],
        findings=[f for score in found.values() for f in score.findings],
        local_warnings=safety.local_warnings(skill),
    )


def _score(dimension: Dimension, skill: Skill, config: EvaluationConfig,
           llm: Any) -> DimensionScore:
    """The dimension's score from its module. Not built is `skipped`. A module that is
    there but fails is `error`, as a broken import must not pass for an unbuilt one."""
    module = SCORERS[dimension]
    name = f"{__name__}.{module}"
    if importlib.util.find_spec(name) is None:
        return DimensionScore(dimension, None, Status.SKIPPED, reason="Not scored",
                              detail="not built yet")
    try:
        return importlib.import_module(name).score(skill, config, llm=llm)
    except JudgeUnavailable:  # judge.md 3
        return DimensionScore(dimension, None, Status.SKIPPED, reason="Not scored",
                              detail="no judge model is available")
    except Exception as e:
        return DimensionScore(dimension, None, Status.ERROR, reason="Not scored",
                              detail=f"failed: {type(e).__name__}")


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
