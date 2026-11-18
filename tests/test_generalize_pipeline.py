import json

import pytest

import tools
from metaskill.generalize import extras, pipeline
from metaskill.generalize.detect import Action, Category, detect_file, detect_file_with_claims
from metaskill.generalize.inventory import InventoryError, SourceFile

EMAIL = "priya.nair@acme-corp.com"
NAME = "Priya Nair"
TEXT = f"# Helper\n\nContact {NAME} at {EMAIL} if the steps fail.\n"


@pytest.fixture(autouse=True)
def clean_registry():
    """Tests register fake extras; leave the shared registry as it was."""
    registry = extras.registry()
    saved = dict(registry)
    registry.clear()
    yield registry
    registry.clear()
    registry.update(saved)


@pytest.fixture
def skill(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text(TEXT, encoding="utf-8", newline="")
    return root


def fake_extra(name="fake", hits=None, calls=None, error=None):
    def detect(text, skip, deadline):
        if calls is not None:
            calls.append((text, list(skip)))
        if error is not None:
            raise error
        if hits is not None:
            return hits(text)
        start = text.find(NAME)
        return [{"start": start, "end": start + len(NAME), "label": "given name", "score": 0.9}]
    return {"api": extras.API, "name": name, "detect": detect}


def test_regex_only_reports_counts_and_offers_the_addon(skill):
    result = pipeline.generalize_skill(str(skill), "out")
    assert result["status"] == "partial"
    assert result["detectors"] == ["regex"]
    assert result["counts"]["files_scanned"] == 1
    assert result["counts"]["by_category"] == {"email": 1}
    assert "metaskill-laya" in result["note"] and "ask" in result["note"]
    assert EMAIL not in json.dumps(result)


def test_extra_runs_after_regex_and_skips_claimed_spans(skill, clean_registry):
    calls = []
    clean_registry["fake"] = fake_extra(calls=calls)
    result = pipeline.generalize_skill(str(skill), "out")

    assert result["detectors"] == ["regex", "fake"]
    assert result["counts"]["by_category"] == {"email": 1, "personal_data": 1}
    assert result["counts"]["by_detector"] == {"fake": 1, "regex": 1}
    assert result["counts"]["by_action"]["review"] >= 1
    assert "note" not in result
    [(text, skip)] = calls
    start = text.find(EMAIL)
    assert skip == [(start, start + len(EMAIL))]
    assert NAME not in json.dumps(result) and EMAIL not in json.dumps(result)


def test_extra_findings_are_personal_data_left_for_review(clean_registry):
    clean_registry["fake"] = fake_extra()
    source = SourceFile("SKILL.md", TEXT)
    run = extras.run_extra(extras.registered()[0], [source], {}, deadline=float("inf"))
    [finding] = run.findings
    assert finding.category is Category.PERSONAL_DATA
    assert finding.action is Action.REVIEW
    assert finding.detector == "fake"
    assert finding.rule == "fake:given_name"
    assert (finding.line, finding.value) == (3, NAME)


def test_invalid_and_overlapping_hits_are_dropped(clean_registry):
    def hits(text):
        start = text.find(EMAIL)
        return [
            {"start": start, "end": start + 5, "label": "email"},  # inside a claimed span
            {"start": 5, "end": 2, "label": "x"},  # backwards
            {"start": 0, "end": len(text) + 10, "label": "x"},  # out of range
            {"start": "a", "end": 3},  # malformed
            "not a dict",
            {"start": 0, "end": 1, "label": "title"},
            {"start": 0, "end": 2, "label": "title"},  # overlaps the previous hit
        ]
    source = SourceFile("SKILL.md", TEXT)
    start = TEXT.find(EMAIL)
    entry = fake_extra(hits=hits)
    run = extras.run_extra(entry, [source], {"SKILL.md": [(start, start + len(EMAIL))]}, float("inf"))
    assert [(f.start, f.end) for f in run.findings] == [(0, 1)]


def test_extra_failure_keeps_regex_results_and_passes_the_hint(skill, clean_registry):
    error = RuntimeError(f"model choked on {EMAIL}")
    error.hint = "restart Hermes"
    clean_registry["fake"] = fake_extra(error=error)
    result = pipeline.generalize_skill(str(skill), "out")

    assert result["status"] == "partial"
    assert result["detectors"] == ["regex"]
    assert result["counts"]["findings"] == 1
    assert result["warnings"] == ["fake detector failed (RuntimeError): restart Hermes"]
    assert EMAIL not in json.dumps(result)  # the message is not passed on, only the hint


def test_extra_stops_at_the_time_limit(skill, clean_registry, monkeypatch):
    calls = []
    clean_registry["fake"] = fake_extra(calls=calls)
    monkeypatch.setenv(extras.TIME_LIMIT_ENV, "0")
    result = pipeline.generalize_skill(str(skill), "out")
    assert calls == []
    assert result["warnings"] == [
        f"fake stopped at the time limit after 0 of 1 files; raise {extras.TIME_LIMIT_ENV} to scan more"]


def test_extra_gets_the_deadline_and_can_stop_inside_a_file(clean_registry):
    seen = []

    def detect(text, skip, deadline):
        seen.append(deadline)
        if len(seen) == 2:
            raise TimeoutError
        return []
    entry = {"api": extras.API, "name": "fake", "detect": detect}
    files = [SourceFile(name, TEXT) for name in ("a.md", "b.md", "c.md")]
    run = extras.run_extra(entry, files, {}, deadline=1e12)
    assert seen == [1e12, 1e12]  # c.md was not started
    assert run.files_scanned == 1  # b.md was cut off, so it does not count
    assert run.warning == (
        f"fake stopped at the time limit after 1 of 3 files; raise {extras.TIME_LIMIT_ENV} to scan more")


@pytest.mark.parametrize("value, expected", [("", 600.0), ("12.5", 12.5), ("-3", 0.0), ("soon", 600.0)])
def test_time_limit_setting(monkeypatch, value, expected):
    monkeypatch.setenv(extras.TIME_LIMIT_ENV, value) if value else monkeypatch.delenv(
        extras.TIME_LIMIT_ENV, raising=False)
    assert extras.time_limit() == expected


@pytest.mark.parametrize("entry", [
    {"api": 2, "name": "future", "detect": lambda text, skip: []},
    {"api": 1, "name": "broken", "detect": "not callable"},
    {"api": 1, "detect": lambda text, skip: []},
    "not a dict",
])
def test_registered_ignores_entries_it_cannot_run(clean_registry, entry):
    clean_registry["x"] = entry
    assert extras.registered() == []


def test_registry_is_shared_through_sys_modules(clean_registry):
    import sys
    assert sys.modules[extras.REGISTRY].detectors is clean_registry


def test_inventory_over_limit_is_an_error(skill, monkeypatch):
    def over_limit(path):
        raise InventoryError(f"{path}: 900 eligible files is over the limit of 500")
    monkeypatch.setattr(pipeline, "load_inventory", over_limit)
    result = pipeline.generalize_skill(str(skill), "out")
    assert result["status"] == "error"
    assert "over the limit" in result["error"]


def test_skipped_files_are_summarized(skill):
    (skill / "logo.png").write_bytes(b"\x89PNG")
    result = pipeline.generalize_skill(str(skill), "out")
    assert result["counts"]["files_skipped"] == 1
    assert result["warnings"] == ["skipped files: 1 unsupported"]


def test_claims_cover_findings_and_rejected_example_values():
    example_phone = "(702) 555-0100"  # 555 numbers are reserved for examples
    text = f"Call {example_phone} or mail priya@acme-corp.com\n"
    source = SourceFile("notes.md", text)
    findings, claimed = detect_file_with_claims(source)
    assert findings == detect_file(source)
    email = text.find("priya@")
    assert (email, len(text) - 1) in claimed
    example = text.find(example_phone)
    assert (example, example + len(example_phone)) in claimed  # reserved, not reported
    assert not any(f.start == example for f in findings)


def test_tool_routes_generalize_to_the_pipeline(skill, clean_registry):
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": str(skill)}))
    assert result["success"] is False  # nothing was generalized yet (MET-12)
    assert result["status"] == "partial"
    assert "NOT generalized" in result["message"] and result["artifacts"] == []
    assert result["operation"] == "generalize"
    assert result["detectors"] == ["regex"]
    assert result["counts"]["findings"] == 1
