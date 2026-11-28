"""Skills from the Hermes hub's default sources, as shared test data
(tests/fixtures/hub-skills).

For any component's tests, beside the bundled Hermes skills (hermes_skills.py). Load one
with `hermes_skills.load`. See SOURCE.md in the fixtures folder for where they come from
and their licences.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "hub-skills"


def skill_dirs() -> list[Path]:
    """Every skill folder, sorted, e.g. .../openai/screenshot."""
    return sorted(p.parent for p in ROOT.rglob("SKILL.md"))


def skill_id(path: Path) -> str:
    """A skill's path relative to the fixtures folder, e.g. "openai/screenshot"."""
    return path.relative_to(ROOT).as_posix()
