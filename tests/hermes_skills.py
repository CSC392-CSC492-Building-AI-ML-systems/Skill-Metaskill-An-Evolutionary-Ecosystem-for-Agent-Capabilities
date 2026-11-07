"""The 58 skills bundled with Hermes, as shared test data (tests/fixtures/hermes-skills).

For any component's tests: generalize, personalize, evaluate. See SOURCE.md in the
fixtures folder for where they come from and their licence.
"""

from __future__ import annotations

from pathlib import Path

from metaskill.evaluate.models import Skill

ROOT = Path(__file__).parent / "fixtures" / "hermes-skills"
_SKIP = {"__pycache__", "node_modules"}


def skill_dirs() -> list[Path]:
    """Every skill folder, sorted, e.g. .../research/arxiv."""
    return sorted(p.parent for p in ROOT.rglob("SKILL.md"))


def skill_id(path: Path) -> str:
    """A skill's path relative to the fixtures folder, e.g. "research/arxiv"."""
    return path.relative_to(ROOT).as_posix()


def load(path: Path) -> Skill:
    """Like Skill.load(), without PyYAML: `frontmatter` is left empty, `body` is the whole
    SKILL.md, and bundled files are listed the same way."""
    files = sorted(
        f.relative_to(path) for f in path.rglob("*")
        if f.is_file() and f.name != "SKILL.md"
        and not any(p.startswith(".") or p in _SKIP for p in f.relative_to(path).parts))
    return Skill(path=path, name=path.name, body=(path / "SKILL.md").read_text(encoding="utf-8-sig"),
                 files=files)
