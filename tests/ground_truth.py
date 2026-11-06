"""Reviewed detection ground truth for real Hermes skills (tests/fixtures/ground-truth).

Each expected.json annotates one skill from tests/fixtures/hermes-skills by file, line and
category, never by value. Every file of an annotated skill was reviewed in full, so a replace
finding that matches no expected entry counts as a false replacement. See README.md there.

Run `python tests/ground_truth.py` from the repository root to print the current measurement.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]

from hermes_skills import ROOT as SKILLS_ROOT, _SKIP

ROOT = Path(__file__).parent / "fixtures" / "ground-truth"
FLAGGED = ("replace", "review")  # actions that surface a value; keep findings are interfaces

Location = tuple[str, int, str]  # file, line, category


def case_paths() -> list[Path]:
    return sorted(ROOT.rglob("expected.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def skill_dir(case: dict) -> Path:
    return SKILLS_ROOT / case["skill"]


def file_hashes(path: Path) -> dict[str, str]:
    """sha256 of every file in a skill, keyed by its path relative to the skill."""
    return {
        f.relative_to(path).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(path.rglob("*"))
        if f.is_file() and not any(p.startswith(".") or p in _SKIP for p in f.relative_to(path).parts)}


@dataclass
class Score:
    expected: int = 0
    detected: int = 0  # expected entries matched by a replace or review finding
    action_agreed: int = 0  # detected entries whose finding has the expected action
    missed: list[Location] = field(default_factory=list)
    false_replacements: list[Location] = field(default_factory=list)
    unexpected_reviews: list[Location] = field(default_factory=list)

    def __iadd__(self, other: Score) -> Score:
        self.expected += other.expected
        self.detected += other.detected
        self.action_agreed += other.action_agreed
        self.missed += other.missed
        self.false_replacements += other.false_replacements
        self.unexpected_reviews += other.unexpected_reviews
        return self


def score(case: dict, findings) -> Score:
    """Match findings to expected entries one-to-one on file, line and category."""
    flagged = sorted(
        (f for f in findings if f.action in FLAGGED),
        key=lambda f: (f.file, f.line, f.action != "replace", f.column))
    used: set[int] = set()
    result = Score(expected=len(case["expected"]))
    for entry in case["expected"]:
        key = (entry["file"], entry["line"], entry["category"])
        match = next((i for i, f in enumerate(flagged)
                      if i not in used and (f.file, f.line, f.category) == key), None)
        if match is None:
            result.missed.append(key)
            continue
        used.add(match)
        result.detected += 1
        result.action_agreed += flagged[match].action == entry["action"]
    for i, f in enumerate(flagged):
        if i not in used:
            bucket = result.false_replacements if f.action == "replace" else result.unexpected_reviews
            bucket.append((f.file, f.line, str(f.category)))
    return result


def measure() -> dict[str, Score]:
    """Score the deterministic detector against every annotated skill."""
    from metaskill.generalize import scan_skill

    cases = [load(p) for p in case_paths()]
    return {c["skill"]: score(c, scan_skill(skill_dir(c)).findings) for c in cases}


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}" if denominator else "n/a (0 expected)"


def main() -> None:
    total = Score()
    for skill, result in measure().items():
        total += result
        print(f"{skill}: detected {_ratio(result.detected, result.expected)}, "
              f"false replacements {len(result.false_replacements)}, "
              f"unexpected reviews {len(result.unexpected_reviews)}")
        for label, locations in (("missed", result.missed),
                                 ("false replacement", result.false_replacements),
                                 ("unexpected review", result.unexpected_reviews)):
            for file, line, category in locations:
                print(f"  {label}: {file}:{line} ({category})")
    print(f"total: detected {_ratio(total.detected, total.expected)}, "
          f"action agreed {_ratio(total.action_agreed, total.detected)}, "
          f"false replacements {len(total.false_replacements)}, "
          f"unexpected reviews {len(total.unexpected_reviews)}")


if __name__ == "__main__":
    main()
