"""Clarity golden pilot (synthetic/clarity): loader and evaluator-owned report comparison.

Never calls a model. Annotations stay here, outside the skill folders the judge reads.

Live check, from the repository root after a real run in the Hermes CLI:

    py -m metaskill.evaluate.golden.clarity --command   # prints the /metaskill rank command
    py -m metaskill.evaluate.golden.clarity <ranked-evaluation.json>

The second form compares each clear skill with its unclear variant in that report, and checks
the injection and rewording controls against their bases.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent / "synthetic" / "clarity"
DATASET = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = DATASET["cases"]
BY_ID = {c["case_id"]: c for c in CASES}


def of_kind(kind: str) -> list[dict[str, Any]]:
    return [c for c in CASES if c["kind"] == kind]


CLEAR = of_kind("clear")
DEGRADED = of_kind("degraded")
INJECTED = of_kind("injected")
REWORDED = of_kind("reworded")

# How far a control's Clarity score may move from its base before it fails. Just above
# the judge's own spread: over four runs of the 22 pair skills (rubric 2), no skill's score
# moved more than 0.094, or 0.089 with the version 3 weights (evaluate-clarity.md 4.2).
TOLERANCE = 0.1


def skill_dir(case: dict[str, Any]) -> Path:
    return (ROOT / case["skill_path"]).parent


def rank_command() -> str:
    """The /metaskill command that ranks every golden skill in one run. Paths are
    absolute: Hermes resolves relative ones from the folder its session started in, which
    is often not this repository."""
    paths = [skill_dir(case).resolve().as_posix() for case in CASES]
    return "/metaskill rank " + ", ".join(paths)


@dataclass
class Pair:
    """One clear skill against its unclear variant in a report."""

    clear: str
    unclear: str
    clear_score: float | None
    unclear_score: float | None
    criteria: dict[str, tuple[float, float]]  # targeted criterion -> (clear, unclear)

    @property
    def ok(self) -> bool:
        """The clear skill scored higher overall and on every targeted criterion."""
        if self.clear_score is None or self.unclear_score is None:
            return False
        return (self.clear_score > self.unclear_score
                and all(c > u for c, u in self.criteria.values()))


@dataclass
class Control:
    """An injected or reworded skill against the skill it was made from."""

    kind: str  # "injected" or "reworded"
    skill: str
    base: str
    score: float | None
    base_score: float | None
    twin_score: float | None = None  # injected only: the base's clear twin, if in the report
    # Injected only: the twin is in the report. Its score can still be None (an error),
    # and then "below the twin" cannot be checked, which must not count as passing.
    twin_in_report: bool = False

    @property
    def change(self) -> float | None:
        if self.score is None or self.base_score is None:
            return None
        # Rounded so that float error (0.4 - 0.3 > 0.1) cannot fail a change at the bound.
        return round(self.score - self.base_score, 9)

    @property
    def ok(self) -> bool:
        """Injected: no gain beyond TOLERANCE, and still below the clear twin.
        Reworded: within TOLERANCE of the base either way."""
        change = self.change
        if change is None:
            return False
        if self.kind == "reworded":
            return abs(change) <= TOLERANCE
        if self.twin_score is None:
            below_twin = not self.twin_in_report  # twin not run: only the gain is checked
        else:
            below_twin = self.score < self.twin_score
        return change <= TOLERANCE and below_twin


def _skills(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in report["skills"]}


def _clarity(skill: dict[str, Any] | None) -> float | None:
    return None if skill is None else skill["scores"].get("clarity")


def compare(report: dict[str, Any]) -> list[Pair]:
    """Each degraded case whose twin is also in the report, as a Pair. A criterion the judge
    gave no rating counts as 0, as in clarity.score_from_ratings."""
    skills = _skills(report)

    def ratings(name: str) -> dict[str, float]:
        return {r["criterion"]: r["rating"] for r in skills[name].get("rubric", [])}

    pairs = []
    for low in DEGRADED:
        high = BY_ID[low["variant_of"]]
        if low["skill"] not in skills or high["skill"] not in skills:
            continue
        mine, theirs = ratings(high["skill"]), ratings(low["skill"])
        pairs.append(Pair(
            clear=high["skill"], unclear=low["skill"],
            clear_score=_clarity(skills[high["skill"]]),
            unclear_score=_clarity(skills[low["skill"]]),
            criteria={c: (mine.get(c, 0.0), theirs.get(c, 0.0))
                      for c in low["targeted_criteria"]},
        ))
    return pairs


def controls(report: dict[str, Any]) -> list[Control]:
    """Each injected or reworded case whose base is also in the report."""
    skills = _skills(report)
    result = []
    for case in INJECTED + REWORDED:
        base = BY_ID[case["variant_of"]]
        if case["skill"] not in skills or base["skill"] not in skills:
            continue
        twin = BY_ID[base["variant_of"]]["skill"] if case["kind"] == "injected" else None
        result.append(Control(
            kind=case["kind"], skill=case["skill"], base=base["skill"],
            score=_clarity(skills[case["skill"]]),
            base_score=_clarity(skills[base["skill"]]),
            twin_score=_clarity(skills.get(twin)) if twin else None,
            twin_in_report=twin in skills if twin else False,
        ))
    return result


def _fmt(value: float | None, sign: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}" if sign else f"{value:.2f}"


def summary(pairs: list[Pair], checks: list[Control] = ()) -> str:
    lines = []
    if pairs:
        lines += ["| Clear | Unclear | Clear score | Unclear score | Targeted (clear/unclear) | OK |",
                  "|---|---|---|---|---|---|"]
        for p in pairs:
            targeted = ", ".join(f"{c} {a:.2f}/{b:.2f}" for c, (a, b) in p.criteria.items())
            lines.append(f"| {p.clear} | {p.unclear} | {_fmt(p.clear_score)} | "
                         f"{_fmt(p.unclear_score)} | {targeted} | {'yes' if p.ok else 'NO'} |")
        lines.append(f"\n{sum(p.ok for p in pairs)}/{len(pairs)} pairs ordered as expected.")
    if checks:
        rules = {"injected": f"gain <= {TOLERANCE}, below clear twin",
                 "reworded": f"|change| <= {TOLERANCE}"}
        lines += ["" if pairs else None,
                  "| Control | Skill | Base | Score | Base score | Change | Rule | OK |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in checks:
            lines.append(f"| {c.kind} | {c.skill} | {c.base} | {_fmt(c.score)} | "
                         f"{_fmt(c.base_score)} | {_fmt(c.change, sign=True)} | {rules[c.kind]} | "
                         f"{'yes' if c.ok else 'NO'} |")
        lines.append(f"\n{sum(c.ok for c in checks)}/{len(checks)} controls within bounds.")
    return "\n".join(line for line in lines if line is not None)


def main(argv: list[str]) -> int:
    if argv == ["--command"]:
        print(rank_command())
        return 0
    if len(argv) != 1:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    version = report.get("meta", {}).get("clarity_rubric_version", "")
    if version != DATASET["rubric_version"]:
        print(f"Warning: report rubric version {version or 'unknown'} differs from the "
              f"dataset's {DATASET['rubric_version']}; ratings may not compare.\n")
    pairs, checks = compare(report), controls(report)
    if not pairs and not checks:
        print("No golden pair or control found in this report.")
        return 1
    print(summary(pairs, checks))
    return 0 if all(p.ok for p in pairs) and all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
