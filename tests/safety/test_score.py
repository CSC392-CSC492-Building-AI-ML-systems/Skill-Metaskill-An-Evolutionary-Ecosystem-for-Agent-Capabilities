from pathlib import Path

from metaskill.evaluate import safety
from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, Finding, SafetyTier, Severity, Skill, Status)
from metaskill.evaluate.safety.checks import CHECKS_VERSION, ChecksBackend
from metaskill.evaluate.safety.cisco import INSTALL_COMMAND, CiscoBackend
from metaskill.evaluate.safety.findings import SEVERITY_SCORE, BackendResult, BackendStatus

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
    assert result.value == SEVERITY_SCORE[Severity.MEDIUM]


def test_llm_fallback_is_partial_and_keeps_static_findings():
    result = run(FakeBackend("cisco", LLM_UNAVAILABLE, [finding()], tier=SafetyTier.STATIC,
                             detail="LLM analysis failed"),
                 config=DEEP)
    assert result.status is Status.PARTIAL
    assert result.value == SEVERITY_SCORE[Severity.HIGH]


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
    assert result.value == SEVERITY_SCORE[Severity.HIGH]


def test_routed_findings_are_kept_but_not_scored():
    secret = finding(Severity.CRITICAL, category="hardcoded_secrets",
                     dimension=Dimension.CONFIG_CLEANLINESS)
    result = run(FakeBackend("cisco", OK, [secret]))
    assert result.value == 1.0
    assert result.findings == [secret]
    assert result.reason == "No safety findings at static tier"


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
    assert result.reason == "No safety findings at static tier"


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


def test_default_backends_are_cisco_and_own_checks():
    llm = object()  # stands in for Hermes's ctx.llm
    cisco, own = safety.default_backends(EvaluationConfig(), llm)
    assert isinstance(cisco, CiscoBackend)
    assert isinstance(own, ChecksBackend)
    assert cisco.llm is llm
    assert cisco.task == "metaskill_judge"  # same model setting as judge.py


def test_missing_dependencies_lists_cisco_when_not_installed(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: False)
    [dep] = safety.missing_dependencies()
    assert dep.backend == "cisco"
    assert dep.install_command == "" and INSTALL_COMMAND in dep.reason


def test_missing_dependencies_is_empty_when_installed(monkeypatch):
    monkeypatch.setattr(CiscoBackend, "available", lambda self: True)
    assert safety.missing_dependencies() == []


def test_versions_has_both_header_fields():
    versions = safety.versions()
    assert set(versions) == {"scanner_version", "checks_version"}
    assert versions["checks_version"] == CHECKS_VERSION


# --- What the deep tier's model calls used (MET-51) -------------------------------------


from types import SimpleNamespace  # noqa: E402

from metaskill.evaluate.judge import TokenUsage  # noqa: E402
from metaskill.evaluate.safety.hermes_bridge import LlmUsage  # noqa: E402


class Spending(FakeBackend):
    """A backend whose model calls happen during its scan, as the review's and Cisco's do:
    `spend()` runs inside scan()."""

    def __init__(self, name, spend, **attrs):
        super().__init__(name, OK)
        self.spend = spend
        for key, value in attrs.items():
            setattr(self, key, value)

    def scan(self, skill, tier):
        self.spend(self)
        return super().scan(skill, tier)


def review_spending(model, usage):
    """The review backend: its judge reports `model` and has used `usage` by the end."""
    def spend(backend):
        backend.judge.judge_model, backend.judge.total_usage = model, usage
    return Spending("llm-review", spend, judge=SimpleNamespace(judge_model="", total_usage=TokenUsage()))


def cisco_spending(*calls):
    """Cisco's backend: its tracker records `calls` ((provider, model, in, out)) during the scan."""
    def spend(backend):
        for provider, model, tokens_in, tokens_out in calls:
            backend.llm_usage.record(SimpleNamespace(provider=provider, model=model), tokens_in, tokens_out)
    return Spending("cisco", spend, llm_usage=LlmUsage())


def test_static_tier_reports_no_judge():
    result = run(FakeBackend("cisco", OK), FakeBackend("own-checks", OK))
    assert result.judge_model == "" and result.judge_usage == {}


def test_the_review_and_cisco_judges_are_merged_into_the_score():
    review = review_spending("openai/gpt-5.4-nano", TokenUsage(1200, 80, 3))
    cisco = cisco_spending(("openai", "gpt-5.4-nano", 3000, 300), ("openai", "gpt-5.4-nano", 2000, 100))
    result = run(cisco, review, FakeBackend("own-checks", OK), config=DEEP)
    assert result.judge_model == "openai/gpt-5.4-nano"
    assert result.judge_usage == {"input_tokens": 6200, "output_tokens": 480, "calls": 5}


def test_a_judge_that_made_no_call_is_not_reported():
    assert run(review_spending("", TokenUsage()), cisco_spending(), config=DEEP).judge_usage == {}


def test_a_fallback_model_is_visible():
    review = review_spending("openai/a, openai/b", TokenUsage(1, 1, 2))
    assert run(review, config=DEEP).judge_model == "openai/a, openai/b"


def test_a_backend_reused_across_skills_reports_only_this_scans_calls():
    # Judge and LlmUsage count for their whole life; a caller that builds backends once
    # (backends_from_context) and scores several skills must not get earlier skills' usage.
    cisco = cisco_spending(("openai", "m", 1000, 10))
    first, second = run(cisco, config=DEEP), run(cisco, config=DEEP)
    assert first.judge_usage == second.judge_usage == {"input_tokens": 1000, "output_tokens": 10, "calls": 1}
    assert cisco.llm_usage.calls == 2  # the tracker itself still counts everything


def test_the_cisco_backend_tracks_its_own_calls():
    backend = CiscoBackend()
    assert isinstance(backend.llm_usage, LlmUsage) and backend.llm_usage.calls == 0
    assert CiscoBackend().llm_usage is not backend.llm_usage  # one tracker per backend


def test_a_real_judge_is_read_as_the_review_uses_it():
    from metaskill.evaluate.judge import Judge

    class Llm:  # ctx.llm: one reply that fits the schema, with its usage
        def complete_structured(self, **kwargs):
            return SimpleNamespace(parsed={"ok": True}, text="", provider="openai", model="gpt-5.4-nano",
                                   content_type="json", usage=SimpleNamespace(input_tokens=300, output_tokens=20))

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    review = Spending("llm-review", lambda b: b.judge.ask("Check it.", "material", schema, "review"),
                      judge=Judge(Llm(), retries=0))
    result = run(review, config=DEEP)
    assert result.judge_model == "openai/gpt-5.4-nano"
    assert result.judge_usage == {"input_tokens": 300, "output_tokens": 20, "calls": 1}
