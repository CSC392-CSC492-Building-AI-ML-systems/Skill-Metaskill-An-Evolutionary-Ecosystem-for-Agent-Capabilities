"""Safety dimension (docs/evaluate-safety.md). Entry point: score(skill, config).

Runs each safety backend, merges their findings and turns the backend statuses
into one DimensionScore (docs/safety/score.md). The only module that knows about
every backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaskill.evaluate.models import (
    Dimension, DimensionScore, EvaluationConfig, Finding, LocalWarning, SafetyTier, Skill, Status)
from metaskill.evaluate.safety import local
from metaskill.evaluate.safety.checks import CHECKS_VERSION, ChecksBackend, delete_targets
from metaskill.evaluate.safety.cisco import INSTALL_COMMAND, PACKAGE, CiscoBackend
from metaskill.evaluate.safety.findings import (
    BackendResult, BackendStatus, SafetyBackend, merge, score_from_findings)
from metaskill.evaluate.safety.hermes_bridge import PluginLlm

# Statuses that still produced findings the score can rely on. A deep scan that
# fell back to static is usable: its static findings are real.
_USABLE = {BackendStatus.OK, BackendStatus.LLM_UNAVAILABLE}


@dataclass
class Dependency:
    """Something a backend needs that is not installed, for the install prompt."""

    backend: str
    package: str
    install_command: str


def score(skill: Skill, config: EvaluationConfig,
          backends: list[SafetyBackend] | None = None,
          llm: PluginLlm | None = None) -> DimensionScore:
    """Safety score for one skill.

    llm is Hermes's ctx.llm, needed for the deep tier's model calls. backends
    defaults to the ones for config.safety_tier; tests pass fakes.
    """
    tier = config.safety_tier
    if backends is None:
        backends = default_backends(config, llm)
    results = [backend.scan(skill, tier) for backend in backends]

    usable = [r for r in results if r.status in _USABLE]
    findings = merge([f for r in usable for f in r.findings])
    status = _status(results, usable)
    value = score_from_findings(findings) if usable else None

    return DimensionScore(
        dimension=Dimension.SAFETY,
        value=value,
        status=status,
        reason=_reason(findings, usable, tier),
        detail="\n".join(f"{r.backend}: {r.detail}" for r in results if r.detail),
        findings=findings,
    )


def local_warnings(skill: Skill, config_values: dict[str, str] | None = None) -> list[LocalWarning]:
    """Unscored warnings about what the skill would delete on this machine.

    config_values are the user's personalization values, for placeholders.
    See docs/safety/local.md.
    """
    return local.local_warnings(skill, delete_targets(skill), config_values)


def default_backends(config: EvaluationConfig, llm: PluginLlm | None = None) -> list[SafetyBackend]:
    """The backends for a tier: Cisco and own checks, plus the LLM review at deep.

    The LLM review (review.py) is not built yet.
    """
    return [CiscoBackend(llm), ChecksBackend()]


def missing_dependencies() -> list[Dependency]:
    """What is not installed, so the tool can ask the user before evaluating.

    Safety never installs anything itself (evaluate-safety.md, section 3.1).
    """
    if CiscoBackend().available():
        return []
    return [Dependency(CiscoBackend.name, PACKAGE, INSTALL_COMMAND)]


def versions() -> dict[str, str]:
    """Backend versions for the report header (ReportMeta)."""
    return {
        "scanner_version": CiscoBackend().version(),
        "checks_version": CHECKS_VERSION,
    }


def _status(results: list[BackendResult], usable: list[BackendResult]) -> Status:
    """Dimension status from backend statuses (docs/safety/score.md)."""
    if not usable:
        if results and any(r.status is BackendStatus.ERROR for r in results):
            return Status.ERROR
        return Status.SKIPPED
    if all(r.status is BackendStatus.OK for r in results):
        return Status.OK
    return Status.PARTIAL


def _reason(findings: list[Finding], usable: list[BackendResult], tier: SafetyTier) -> str:
    if not usable:
        return "Not scored: no safety backend could run"
    worst = next((f for f in findings if f.dimension is Dimension.SAFETY), None)
    if worst is None:
        ran = tier if all(r.tier is tier for r in usable) else SafetyTier.STATIC
        return f"No findings at {ran} tier"
    where = f"at {worst.location}" if worst.location else "in the skill as a whole"
    return f"Worst finding: {worst.severity} {worst.category} {where}"
