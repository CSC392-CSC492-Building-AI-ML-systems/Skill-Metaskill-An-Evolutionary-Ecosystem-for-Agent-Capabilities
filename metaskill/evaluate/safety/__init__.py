"""Safety dimension (docs/evaluate-safety.md). Entry point: score(skill, config).

Runs each safety backend, merges their findings and turns the backend statuses
into one DimensionScore (docs/safety/score.md). The only module that knows about
every backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..judge import JUDGE_TASK, Judge
from ..models import (
    Dimension, DimensionScore, EvaluationConfig, Finding, LocalWarning, SafetyTier, Skill, Status)
from . import local
from .checks import CHECKS_VERSION, ChecksBackend, delete_targets
from .cisco import INSTALL_COMMAND, PACKAGE, CiscoBackend
from .findings import (
    BackendResult, BackendStatus, SafetyBackend, merge, score_from_findings)
from .hermes_bridge import PluginLlm
from .review import ReviewBackend

# Statuses that still produced findings the score can rely on. A deep scan that
# fell back to static is usable: its static findings are real.
_USABLE = {BackendStatus.OK, BackendStatus.LLM_UNAVAILABLE}


@dataclass
class Dependency:
    """Something a backend needs that is not installed, for the install prompt."""

    backend: str
    package: str
    install_command: str  # "" when the user has to install it by hand (install_url)
    # Required: evaluate does not run without it. Otherwise it only runs with less, as
    # safety does without Cisco's scanner.
    required: bool = False
    install_url: str = ""  # where to get it, when no command installs it
    reason: str = ""  # what is wrong, e.g. "Docker is not usable: ..."


def score(skill: Skill, config: EvaluationConfig,
          backends: list[SafetyBackend] | None = None,
          llm: PluginLlm | None = None, task: str | None = None) -> DimensionScore:
    """Safety score for one skill.

    llm is Hermes's ctx.llm, needed for the deep tier's model calls. task is the registered
    auxiliary task to route those calls through; it defaults to None so a direct
    `score(skill, config, llm=ctx.llm)` never sends an unregistered task on an old Hermes.
    The plugin should build backends with `backends_from_context(ctx, config)`, which sets
    the task only where Hermes supports it (register_auxiliary_task), matching
    Judge.from_context. backends defaults to the ones for config.safety_tier; tests pass fakes.
    """
    tier = config.safety_tier
    if backends is None:
        backends = default_backends(config, llm, task)
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


def default_backends(config: EvaluationConfig, llm: PluginLlm | None = None,
                     task: str | None = JUDGE_TASK) -> list[SafetyBackend]:
    """The backends for a tier: Cisco and own checks, plus the LLM review at deep.

    Without llm (Hermes's ctx.llm), the review is still listed at deep so its absence
    shows as llm-unavailable, and safety as partial. `task` is the registered auxiliary
    task, or None on a Hermes without auxiliary tasks; it, judge_provider and judge_model
    are given to both Cisco and the judge so they always run on the same model.
    """
    cisco = CiscoBackend(llm, task=task, provider=config.judge_provider, model=config.judge_model)
    backends: list[SafetyBackend] = [cisco, ChecksBackend()]
    if config.safety_tier is SafetyTier.DEEP:
        judge = Judge(llm, task=task, provider=config.judge_provider, model=config.judge_model,
                      retries=config.judge_retries, max_chars=config.judge_max_chars) if llm else None
        backends.append(ReviewBackend(judge))
    return backends


def backends_from_context(ctx: Any, config: EvaluationConfig) -> list[SafetyBackend]:
    """`default_backends` wired from a Hermes plugin context, so a caller does not have to
    get the task right by hand. Takes `ctx.llm`, and the auxiliary task only where Hermes
    supports it (`register_auxiliary_task`) -- matching `Judge.from_context` -- so no
    unregistered task ever reaches `ctx.llm`. This is the constructor callers should use.
    """
    llm = getattr(ctx, "llm", None)
    task = JUDGE_TASK if hasattr(ctx, "register_auxiliary_task") else None
    return default_backends(config, llm, task)


def missing_dependencies() -> list[Dependency]:
    """What is not installed, so the tool can tell the user before evaluating.

    Safety never installs anything itself (evaluate-safety.md, section 3.1).
    """
    if CiscoBackend().available():
        return []
    # No install_command: Hermes asks the user to consent, so the commands need the user's
    # terminal. Run by the agent without one, they can leave the plugin disabled.
    return [Dependency(CiscoBackend.name, PACKAGE, "",
                       reason=f"run `{INSTALL_COMMAND}` in a terminal, then restart Hermes")]


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
        return f"No safety findings at {ran} tier"
    where = f"at {worst.location}" if worst.location else "in the skill as a whole"
    return f"Worst finding: {worst.severity} {worst.category} {where}"
