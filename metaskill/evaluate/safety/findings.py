"""Backend interface, merging and scoring for the Safety dimension.

Implements section 5 of docs/evaluate-safety.md. Pure logic: nothing here runs
a scanner or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from metaskill.evaluate.models import Dimension, Finding, SafetyTier, Severity

# Most severe first. Used to pick the worst finding and to order merged output.
SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}

# [OPEN] placeholder values (evaluate-safety.md, open question 3).
SEVERITY_SCORE = {
    Severity.CRITICAL: 0.0,
    Severity.HIGH: 0.3,
    Severity.MEDIUM: 0.6,
    Severity.LOW: 0.9,
    Severity.INFO: 0.9,
}

# Categories from different backends that describe the same problem (section 2.1).
# Findings in one group at the same location are merged.
OVERLAPPING_CATEGORIES = [
    {"untrusted-source", "supply_chain_attack"},
    {"excess-scope", "unauthorized_tool_use", "autonomy_abuse"},
]

# Separator for the sources and rules of a merged finding, e.g. "cisco, own-checks".
_JOIN = ", "


class BackendStatus(StrEnum):
    OK = "ok"
    NOT_INSTALLED = "not-installed"
    LLM_UNAVAILABLE = "llm-unavailable"  # deep requested, ran as static
    ERROR = "error"


@dataclass
class BackendResult:
    """What one backend returns for one skill."""

    backend: str
    status: BackendStatus
    tier: SafetyTier  # the tier that actually ran
    findings: list[Finding] = field(default_factory=list)
    detail: str = ""  # error message, when status is ERROR


class SafetyBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def scan(self, skill_dir: Path, tier: SafetyTier) -> BackendResult: ...


def merge(findings: list[Finding]) -> list[Finding]:
    """Merge findings that report the same problem, most severe first.

    Two findings are the same problem when they have the same dimension, the same
    category (or overlapping ones), and the same location. The merged finding
    keeps the most severe one's fields and lists every source and rule.
    """
    merged: list[Finding] = []
    for finding in sorted(findings, key=lambda f: -SEVERITY_RANK[f.severity]):
        for i, kept in enumerate(merged):
            if _same_problem(kept, finding):
                merged[i] = _combine(kept, finding)
                break
        else:
            merged.append(finding)
    return merged


def worst_severity(findings: list[Finding]) -> Severity | None:
    """The most severe safety finding, or None if there are none."""
    severities = [f.severity for f in findings if f.dimension is Dimension.SAFETY]
    return max(severities, key=SEVERITY_RANK.__getitem__, default=None)


def score_from_findings(findings: list[Finding]) -> float:
    """Safety score in [0, 1], set by the worst finding (section 5).

    Findings handed to another dimension do not count.
    """
    worst = worst_severity(findings)
    return 1.0 if worst is None else SEVERITY_SCORE[worst]


def _same_problem(a: Finding, b: Finding) -> bool:
    return (
        a.dimension == b.dimension
        and _category_key(a.category) == _category_key(b.category)
        and _location_key(a.location) == _location_key(b.location)
    )


def _category_key(category: str) -> frozenset[str]:
    for group in OVERLAPPING_CATEGORIES:
        if category in group:
            return frozenset(group)
    return frozenset({category})


def _location_key(location: str) -> str:
    # Cisco and our checks may write "./SKILL.md:42" and "SKILL.md:42".
    return location.strip().removeprefix("./")


def _combine(kept: Finding, other: Finding) -> Finding:
    """kept is at least as severe as other, since merge() sorts first."""
    return replace(
        kept,
        source=_join(kept.source, other.source),
        rule=_join(kept.rule, other.rule),
        quote=kept.quote or other.quote,
        message=kept.message or other.message,
    )


def _join(a: str, b: str) -> str:
    parts = a.split(_JOIN) + [p for p in b.split(_JOIN) if p not in a.split(_JOIN)]
    return _JOIN.join(parts)
