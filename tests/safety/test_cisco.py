import copy
import json
from pathlib import Path

import pytest

from metaskill.evaluate.models import Dimension, SafetyTier, Severity, Skill
from metaskill.evaluate.safety.cisco import INSTALL_COMMAND, CiscoBackend, LLMSetupError, convert
from metaskill.evaluate.safety.findings import BackendStatus, score_from_findings

FIXTURES = Path(__file__).parent / "fixtures" / "cisco"
SKILL = Skill(path=Path("some-skill"), name="some-skill", body="")


def fixture(name):
    """Scanner output recorded from real skills with Cisco skill-scanner 2.2.1."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeCisco(CiscoBackend):
    """CiscoBackend with the scanner calls replaced, so tests need no scanner."""

    def __init__(self, scan_output=None, installed=True, setup_error=None, scan_error=None):
        super().__init__()
        self.scan_output = scan_output
        self.installed = installed
        self.setup_error = setup_error
        self.scan_error = scan_error
        self.analyzers_used = "not called"

    def available(self):
        return self.installed

    def _build_deep_analyzers(self):
        if self.setup_error:
            raise LLMSetupError(self.setup_error)
        return ["deep analyzers"]

    def _run_scanner(self, path, analyzers):
        self.analyzers_used = analyzers
        if self.scan_error:
            raise self.scan_error
        return self.scan_output


# --- Conversion -------------------------------------------------------------


def test_clean_skill_has_no_findings():
    result = convert(fixture("obsidian-static"), SafetyTier.STATIC)
    assert result.status is BackendStatus.OK
    assert result.findings == []
    assert result.detail == ""


def test_findings_are_converted_field_by_field():
    result = convert(fixture("p5js-static"), SafetyTier.STATIC)
    [finding] = result.findings
    assert finding.dimension is Dimension.SAFETY
    assert finding.category == "command_injection"
    assert finding.severity is Severity.CRITICAL
    assert finding.source == "cisco"
    assert finding.rule == "COMMAND_INJECTION_JS_FUNCTION_CONSTRUCTOR"
    assert finding.location == "scripts/export-frames.js:128"
    assert "waitForFunction" in finding.quote
    assert finding.message.startswith("Dynamic code execution")


def test_location_without_line_and_empty_snippet():
    findings = convert(fixture("arxiv-static"), SafetyTier.STATIC).findings
    locations = [f.location for f in findings]
    assert "SKILL.md" in locations
    assert "scripts/search_arxiv.py:46" in locations
    whole_file = next(f for f in findings if f.location == "SKILL.md")
    assert whole_file.quote == ""


def test_finding_without_file_has_empty_location():
    scan = fixture("p5js-static")
    scan["findings"][0]["file_path"] = None
    scan["findings"][0]["line_number"] = None
    assert convert(scan, SafetyTier.STATIC).findings[0].location == ""


def test_routed_categories_do_not_count_toward_safety():
    scan = fixture("p5js-static")
    scan["findings"][0]["category"] = "hardcoded_secrets"
    findings = convert(scan, SafetyTier.STATIC).findings
    assert findings[0].dimension is Dimension.CONFIG_CLEANLINESS
    assert score_from_findings(findings) == 1.0


def test_safe_severity_findings_are_dropped():
    scan = fixture("p5js-static")
    scan["findings"][0]["severity"] = "SAFE"
    assert convert(scan, SafetyTier.STATIC).findings == []


def test_failed_static_analyzer_is_noted_in_detail():
    scan = fixture("obsidian-static")
    scan["analyzers_failed"] = [{"analyzer": "bytecode", "error": "boom"}]
    result = convert(scan, SafetyTier.STATIC)
    assert result.status is BackendStatus.OK
    assert "bytecode failed: boom" in result.detail


# --- Deep tier and the LLM layer ----------------------------------------------


def test_llm_failure_at_run_time_keeps_static_findings():
    # Recorded with the LLM pointed at an unreachable endpoint: the scan returns
    # normally, with the failure in analyzers_failed and seven notice findings.
    result = convert(fixture("p5js-deep-llm-failed"), SafetyTier.DEEP)
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC
    assert [f.rule for f in result.findings] == ["COMMAND_INJECTION_JS_FUNCTION_CONSTRUCTOR"]
    assert "LLM analysis failed" in result.detail
    assert "skipped or cut 6 file(s)" in result.detail
    assert "references/animation.md" in result.detail


def test_successful_deep_scan_reports_deep_tier():
    scan = copy.deepcopy(fixture("p5js-static"))
    scan["analyzers_used"].append("llm_analyzer")
    result = convert(scan, SafetyTier.DEEP)
    assert result.status is BackendStatus.OK
    assert result.tier is SafetyTier.DEEP


def test_deep_scan_without_llm_analyzer_is_not_reported_as_deep():
    result = convert(fixture("p5js-static"), SafetyTier.DEEP)
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC


# --- scan() -------------------------------------------------------------------


def test_not_installed():
    result = FakeCisco(installed=False).scan(SKILL, SafetyTier.STATIC)
    assert result.status is BackendStatus.NOT_INSTALLED
    assert result.findings == []
    assert INSTALL_COMMAND in result.detail


def test_static_scan_uses_default_analyzers():
    backend = FakeCisco(fixture("p5js-static"))
    result = backend.scan(SKILL, SafetyTier.STATIC)
    assert backend.analyzers_used is None
    assert result.status is BackendStatus.OK
    assert len(result.findings) == 1


def test_deep_scan_uses_llm_analyzers():
    scan = copy.deepcopy(fixture("p5js-static"))
    scan["analyzers_used"].append("llm_analyzer")
    backend = FakeCisco(scan)
    result = backend.scan(SKILL, SafetyTier.DEEP)
    assert backend.analyzers_used == ["deep analyzers"]
    assert result.tier is SafetyTier.DEEP


def test_llm_setup_failure_falls_back_to_static():
    backend = FakeCisco(fixture("p5js-static"), setup_error="API key required")
    result = backend.scan(SKILL, SafetyTier.DEEP)
    assert backend.analyzers_used is None  # reran with the default static analyzers
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC
    assert "API key required" in result.detail
    assert len(result.findings) == 1


def test_unexpected_error_is_returned_not_raised():
    result = FakeCisco(scan_error=RuntimeError("disk on fire")).scan(SKILL, SafetyTier.STATIC)
    assert result.status is BackendStatus.ERROR
    assert "disk on fire" in result.detail


# --- Real scanner (skipped when it is not installed, as in CI) ----------------


def test_real_scanner_on_a_minimal_skill(tmp_path):
    pytest.importorskip("skill_scanner")
    (tmp_path / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Say hello to the user.\n---\n\n# Hello\n\nGreet the user by name.\n")
    skill = Skill(path=tmp_path, name="hello", body="")
    result = CiscoBackend().scan(skill, SafetyTier.STATIC)
    assert result.status is BackendStatus.OK, result.detail
    assert result.tier is SafetyTier.STATIC
