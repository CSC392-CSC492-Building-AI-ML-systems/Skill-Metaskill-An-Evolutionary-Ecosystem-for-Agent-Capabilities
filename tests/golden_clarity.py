"""The draft Clarity golden dataset (tests/fixtures/golden/clarity), as shared test data.

Temporary home until the golden dataset's location is agreed (docs/evaluate-clarity.md 4).

Live check, after a real run in the Hermes CLI:

    py tests/golden_clarity.py --command          # prints the /metaskill rank command
    py tests/golden_clarity.py <ranked-evaluation.json>

The second form compares each clear skill with its unclear variant in that report.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent / "fixtures" / "golden" / "clarity"
DATASET = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = DATASET["cases"]
BY_ID = {c["case_id"]: c for c in CASES}
HIGH = [c for c in CASES if c["expected_label"] == "high"]
LOW = [c for c in CASES if c["expected_label"] == "low"]


def skill_dir(case: dict[str, Any]) -> Path:
    return (ROOT / case["skill_path"]).parent


def rank_command(root: Path = Path.cwd()) -> str:
    """The /metaskill command that ranks every golden skill in one run."""
    paths = []
    for case in CASES:
        folder = skill_dir(case).resolve()
        try:
            paths.append(folder.relative_to(root.resolve()).as_posix())
        except ValueError:
            paths.append(folder.as_posix())
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


def compare(report: dict[str, Any]) -> list[Pair]:
    """Each low case whose twin is also in the report, as a Pair. A criterion the judge
    gave no rating counts as 0, as in clarity.score_from_ratings."""
    skills = {s["name"]: s for s in report["skills"]}

    def ratings(name: str) -> dict[str, float]:
        return {r["criterion"]: r["rating"] for r in skills[name].get("rubric", [])}

    pairs = []
    for low in LOW:
        high = BY_ID[low["variant_of"]]
        if low["skill"] not in skills or high["skill"] not in skills:
            continue
        mine, theirs = ratings(high["skill"]), ratings(low["skill"])
        pairs.append(Pair(
            clear=high["skill"], unclear=low["skill"],
            clear_score=skills[high["skill"]]["scores"].get("clarity"),
            unclear_score=skills[low["skill"]]["scores"].get("clarity"),
            criteria={c: (mine.get(c, 0.0), theirs.get(c, 0.0))
                      for c in low["targeted_criteria"]},
        ))
    return pairs


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def summary(pairs: list[Pair]) -> str:
    lines = ["| Clear | Unclear | Clear score | Unclear score | Targeted (clear/unclear) | OK |",
             "|---|---|---|---|---|---|"]
    for p in pairs:
        targeted = ", ".join(f"{c} {a:.2f}/{b:.2f}" for c, (a, b) in p.criteria.items())
        lines.append(f"| {p.clear} | {p.unclear} | {_fmt(p.clear_score)} | "
                     f"{_fmt(p.unclear_score)} | {targeted} | {'yes' if p.ok else 'NO'} |")
    lines.append(f"\n{sum(p.ok for p in pairs)}/{len(pairs)} pairs ordered as expected.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv == ["--command"]:
        print(rank_command())
        return 0
    if len(argv) != 1:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    pairs = compare(report)
    if not pairs:
        print("No golden pair found in this report.")
        return 1
    print(summary(pairs))
    return 0 if all(p.ok for p in pairs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
