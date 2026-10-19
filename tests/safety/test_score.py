from pathlib import Path

from metaskill.evaluate import safety
from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, Finding, SafetyTier, Severity, Skill, Status)
from metaskill.evaluate.safety.cisco import INSTALL_COMMAND, CiscoBackend
from metaskill.evaluate.safety.findings import BackendResult, BackendStatus

SKILL = Skill(path=Path("some-skill"), name="some-skill", body="")
STATIC = EvaluationConfig(safety_tier=SafetyTier.STATIC)
DEEP = EvaluationConfig(safety_tier=SafetyTier.DEEP)

OK = BackendStatus.OK
NOT_INSTALLED = BackendStatus.NOT_INSTALLED
LLM_UNAVAILABLE = BackendStatus.LLM_UNAVAILABLE
ERROR = BackendStatus.ERROR


def finding(severity=Severity.HIGH, source="cisco", location="SKILL.md:42",
            category="destructive-unconfirmed", dimension=Dimension.SAFETY):
    return Finding(dimension, category, severity, source, "some-rule", "rm -rf x", location)


class FakeBackend:
    def __init__(self, name, status, findings=(), detail="", tier=None):
        self.name = name
        self.result = (status, list(findings), detail, tier)
        self.scanned = False

    def available(self):
        return self.result[0] is not NOT_INSTALLED

    def scan(self, skill, tier):
        self.scanned = True
        status, findings, detail, ran = self.result
        return BackendResult(self.name, status, ran or tier, findings, detail)


def run(*backends, config=STATIC):
    return safety.score(SKILL, config, backends=list(backends))


# --- Status table (docs/safety/score.md) ----------------------------------------


def test_every_backend_ok():
    result = run(FakeBackend("cisco", OK), FakeBackend("own-checks", OK))
    assert result.status is Status.OK
    assert result.value == 1.0


def test_one_backend_not_installed_is_partial():
    result = run(FakeBackend("cisco", NOT_INSTALLED, detail="install it"),
                 FakeBackend("own-checks", OK, [finding(Severity.MEDIUM, "own-checks")]))
    assert result.status is Status.PARTIAL
    assert result.value == 0.6


def test_llm_fallback_is_partial_and_keeps_static_findings():
    result = run(FakeBackend("cisco", LLM_UNAVAILABLE, [finding()], tier=SafetyTier.STATIC,
                             detail="LLM analysis failed"),
                 config=DEEP)
    assert result.status is Status.PARTIAL
    assert result.value == 0.3


def test_nothing_installed_is_skipped_with_no_score():
    result = run(FakeBackend("cisco", NOT_INSTALLED))
    assert result.status is Status.SKIPPED
    assert result.value is None


def test_every_backend_failing_is_error_with_no_score():
    result = run(FakeBackend("cisco", ERROR), FakeBackend("own-checks", ERROR))
    assert result.status is Status.ERROR
    assert result.value is None


def test_no_backends_is_skipped_not_a_perfect_score():
    result = run()
    assert result.status is Status.SKIPPED
    assert result.value is None


def test_findings_from_a_failed_backend_are_ignored():
    result = run(FakeBackend("cisco", ERROR, [finding(Severity.CRITICAL)]),
                 FakeBackend("own-checks", OK))
    assert result.value == 1.0
    assert result.findings == []


# --- Findings and score --------------------------------------------------------


def test_same_problem_from_two_backends_counts_once():
    result = run(FakeBackend("cisco", OK, [finding(Severity.MEDIUM, "cisco")]),
                 FakeBackend("own-checks", OK, [finding(Severity.HIGH, "own-checks")]))
    [merged] = result.findings
    assert merged.severity is Severity.HIGH
    assert merged.source == "own-checks, cisco"
    assert result.value == 0.3


def test_routed_findings_are_kept_but_not_scored():
    secret = finding(Severity.CRITICAL, category="hardcoded_secrets",
                     dimension=Dimension.CONFIG_CLEANLINESS)
    result = run(FakeBackend("cisco", OK, [secret]))
    assert result.value == 1.0
    assert result.findings == [secret]
    assert result.reason == "No findings at static tier"


# --- Reason and detail ---------------------------------------------------------


def test_reason_names_the_worst_safety_finding():
    result = run(FakeBackend("cisco", OK, [finding(Severity.LOW, location="a.md:1"),
                                           finding(Severity.CRITICAL, location="b.sh:7")]))
    assert result.reason == "Worst finding: CRITICAL destructive-unconfirmed at b.sh:7"


def test_reason_for_a_whole_skill_finding():
    result = run(FakeBackend("cisco", OK, [finding(location="")]))
    assert result.reason.endswith("in the skill as a whole")


def test_reason_reports_the_tier_that_actually_ran():
    result = run(FakeBackend("cisco", LLM_UNAVAILABLE, tier=SafetyTier.STATIC), config=DEEP)
    assert result.reason == "No findings at static tier"


def test_reason_when_nothing_ran():
    assert run(FakeBackend("cisco", NOT_INSTALLED)).reason == (
        "Not scored: no safety backend could run")


def test_detail_has_one_line_per_backend_with_something_to_say():
    result = run(FakeBackend("cisco", NOT_INSTALLED, detail="not installed"),
                 FakeBackend("own-checks", OK),
                 FakeBackend("llm-review", LLM_UNAVAILABLE, detail="no judge configured"))
    assert result.detail.splitlines() == [
        "cisco: not installed", "llm-review: no judge configured"]


def test_detail_is_empty_when_everything_ran_cleanly():
    assert run(FakeBackend("cisco", OK)).detail == ""


# --- Defaults, dependencies and versions ---------------------------------------


def test_default_backends_use_the_judge_model_for_cisco():
    config = EvaluationConfig(judge_provider="anthropic", judge_model="some-model")
    [cisco] = safety.default_backends(config)
    assert isinstance(cisco, CiscoBackend)
    assert (cisco.llm_provider, cisco.llm_model) == ("anthropic", "some-model")


def test_missing_dependencies_lists_cisco_when_not_installed(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: False)
    [dep] = safety.missing_dependencies()
    assert dep.backend == "cisco"
    assert dep.install_command == INSTALL_COMMAND


def test_missing_dependencies_is_empty_when_installed(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: True)
    assert safety.missing_dependencies() == []


def test_versions_has_both_header_fields():
    assert set(safety.versions()) == {"scanner_version", "checks_version"}
