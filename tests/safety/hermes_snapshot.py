"""The own safety checks' findings on the shared real skills, and the saved snapshots: the
skills bundled with Hermes, and those from the Hermes hub's default sources."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import hermes_skills
import hub_skills

from metaskill.evaluate.models import SafetyTier
from metaskill.evaluate.safety.checks import ChecksBackend

SNAPSHOT = Path(__file__).parent / "hermes_expected.json"
HUB_SNAPSHOT = Path(__file__).parent / "hub_expected.json"
# Each set of skills (a module with skill_dirs and skill_id) and its snapshot.
SETS = ((hermes_skills, SNAPSHOT), (hub_skills, HUB_SNAPSHOT))


def results(skills: ModuleType = hermes_skills) -> dict[str, list[list[str]]]:
    """Each skill's findings as [rule, severity, location], sorted, keyed by skill_id."""
    out = {}
    for path in skills.skill_dirs():
        result = ChecksBackend().scan(hermes_skills.load(path), SafetyTier.STATIC)
        if result.status != "ok":
            raise RuntimeError(f"{path}: {result.status}: {result.detail}")
        out[skills.skill_id(path)] = sorted([f.rule, str(f.severity), f.location] for f in result.findings)
    return out


def expected(snapshot: Path = SNAPSHOT) -> dict[str, list[list[str]]]:
    return json.loads(snapshot.read_text(encoding="utf-8"))
