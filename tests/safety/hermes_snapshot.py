"""The own safety checks' findings on the shared Hermes skills, and the saved snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from hermes_skills import load, skill_dirs, skill_id

from metaskill.evaluate.models import SafetyTier
from metaskill.evaluate.safety.checks import ChecksBackend

SNAPSHOT = Path(__file__).parent / "hermes_expected.json"


def results() -> dict[str, list[list[str]]]:
    """Each skill's findings as [rule, severity, location], sorted, keyed by skill_id."""
    out = {}
    for path in skill_dirs():
        result = ChecksBackend().scan(load(path), SafetyTier.STATIC)
        if result.status != "ok":
            raise RuntimeError(f"{path}: {result.status}: {result.detail}")
        out[skill_id(path)] = sorted([f.rule, str(f.severity), f.location] for f in result.findings)
    return out


def expected() -> dict[str, list[list[str]]]:
    return json.loads(SNAPSHOT.read_text())
