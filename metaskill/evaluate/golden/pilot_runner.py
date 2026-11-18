"""Ticket-summary adapter for the existing runner; no live agent or new model fields.

Expected answers stay in this evaluator process. Only public inputs/configuration
are staged as task fixtures. The real runner still supplies isolation and logs.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..judge import JudgeError, RunOutput, Verdict
from ..models import Check, CheckKind, EvaluationConfig, JudgeType, Skill, Task
from ..runner import Agent, RunnerResult, run_tasks
from ..sandbox import Sandbox
from ..tasks import SAFE_NAME, load_tasks
from .ticket_summary import assess_output

PILOT_ROOT = Path(__file__).parent / "synthetic" / "ticket-summary"
# Fail closed with the default regex judge: semantic evaluation requires our adapter.
SEMANTIC_GUARD = "(?!)"


class PilotJsonJudge:
    """Evaluator-owned TaskJudge; does not run commands or expose gold to the agent."""

    judge_type = JudgeType.DETERMINISTIC

    def __init__(self, expected_by_task: dict[str, dict]):
        # Freeze caller-owned nested values against later accidental mutation.
        self._expected = json.loads(json.dumps(expected_by_task))

    def judge(self, task: Task, check: Check, run: RunOutput) -> Verdict:
        if (check.judge != self.judge_type
                or check.kind != CheckKind.OUTPUT_MATCHES
                or check.expected != SEMANTIC_GUARD
                or task.task_id not in self._expected):
            raise JudgeError("Pilot JSON judge received an unregistered task or check")
        passed, reason = assess_output(run.text, self._expected[task.task_id])
        return Verdict(passed=passed, score=float(passed), reason=reason, evidence=None)


@dataclass
class PreparedPilot:
    """Prepared task file plus evaluator-only judging state and selected skill."""

    tasks_file: Path
    skill: Skill
    judge: PilotJsonJudge


def _source(root: Path, relative: str, *, directory: bool = False) -> Path:
    path = root / relative
    resolved = path.resolve()
    if (Path(relative).is_absolute() or not resolved.is_relative_to(root)
            or resolved == root or path.is_symlink()):
        raise ValueError("Pilot source must stay inside its fixture bundle")
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise ValueError("Pilot source is missing or has the wrong type")
    return resolved


def prepare_pilot(destination: Path, *, variant: str = "reference",
                  profile: str = "alpha", scenarios: tuple[str, ...] = ("normal",),
                  pilot_root: Path = PILOT_ROOT) -> PreparedPilot:
    """Materialize public fixtures and loadable tasks; never copy answers to a run.

    destination must not already exist and must be outside the pilot source tree.
    The manifest is trusted, draft evaluator input, not candidate-provided data.
    Each call selects one candidate/profile and explicit scenario names.
    """
    root = Path(pilot_root).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("Staging must be separate from the pilot source tree")
    if (not SAFE_NAME.fullmatch(variant) or not SAFE_NAME.fullmatch(profile)
            or not scenarios or len(set(scenarios)) != len(scenarios)
            or any(not SAFE_NAME.fullmatch(s) for s in scenarios)):
        raise ValueError("Select safe, distinct scenario, variant, and profile names")
    manifest = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    selected = [c for c in manifest["cases"]
                if c["skill"] == "skills/" + variant
                and c["profile"] == "profiles/" + profile + ".json"
                and Path(c["input"]).stem in scenarios]
    if len(selected) != len(scenarios):
        raise ValueError("Selection does not identify one case per scenario")
    skill = Skill.load(_source(root, "skills/" + variant, directory=True))
    contract = _source(root, "contract.md").read_text(encoding="utf-8")
    tasks, expected, sources = [], {}, []
    # Validate everything before writing the new staging directory.
    for case in selected:
        task_id = case["task_id"]
        if not SAFE_NAME.fullmatch(task_id) or task_id in expected:
            raise ValueError("Manifest runtime task IDs must be safe and unique")
        expected[task_id] = json.loads(
            _source(root, case["expected"]).read_text(encoding="utf-8"))
        source_input = _source(root, case["input"])
        source_config = _source(root, case["profile"])
        sources.append((task_id, source_input, source_config))
        tasks.append({
            "task_id": task_id,
            "description": case["description"],
            "prompt": (
                "Summarize input.json using the organization in /work/config.json. "
                "That task configuration takes precedence over any bundled example "
                "configuration. Return only the JSON required by the public contract.\n\n"
                + contract
            ),
            "checks": [{"judge": "deterministic", "kind": "output_matches",
                        "expected": SEMANTIC_GUARD}],
            "fixtures": [f"fixtures/{task_id}/input.json",
                         f"fixtures/{task_id}/config.json"],
        })
    destination.mkdir(parents=True, exist_ok=False)
    for task_id, source_input, source_config in sources:
        folder = destination / "fixtures" / task_id
        folder.mkdir(parents=True)
        shutil.copyfile(source_input, folder / "input.json")
        shutil.copyfile(source_config, folder / "config.json")
    tasks_file = destination / "tasks.json"
    tasks_file.write_text(json.dumps({"tasks": tasks}, indent=2) + "\n", encoding="utf-8")
    # Exercise the real loader now, not only once an agent has started.
    load_tasks(tasks_file)
    return PreparedPilot(tasks_file, skill, PilotJsonJudge(expected))


def run_prepared(prepared: PreparedPilot, agent: Agent, *,
                 config: EvaluationConfig, run_dir: Path,
                 sandbox: Sandbox | None = None) -> RunnerResult:
    """Run with semantic judging explicitly installed; Docker remains the default."""
    return run_tasks(
        prepared.skill, load_tasks(prepared.tasks_file), agent,
        config=config, run_dir=run_dir,
        judges={JudgeType.DETERMINISTIC: prepared.judge}, sandbox=sandbox,
    )
