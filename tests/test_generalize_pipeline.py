"""End-to-end coverage for config templates and the generalization pipeline."""

import json

import pytest

from skill_fixtures.secrets import GENERIC_SECRET, assert_not_leaked
from metaskill.generalize.config import assign_config_fields, build_config_template
from metaskill.generalize.detect import Category, scan_skill
from metaskill.generalize.pipeline import generalize_skill

import tools
from metaskill.generalize import extras, pipeline
from metaskill.generalize.detect import Action, detect_file, detect_file_with_claims
from metaskill.generalize.inventory import InventoryError, SourceFile


def write_skill(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def test_config_template_has_required_metadata_and_blank_examples(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": "Contact user@company.ca",
        "config.json": '{"api_key": "' + GENERIC_SECRET + '"}',
    })
    result = scan_skill(skill)
    source_by_path = {item.path: item for item in result.files}
    replacements = [
        (finding, source_by_path[finding.file].text[finding.start:finding.end])
        for finding in result.findings
        if finding.action.value == "replace"
    ]
    fields, placeholders = assign_config_fields(replacements)
    schema, example = build_config_template(fields)

    assert all(name in schema["required"] for name in fields)
    assert schema["additionalProperties"] is False
    assert all(set(field) >= {"name", "type", "required", "description", "validation", "sensitive"}
               for field in schema["properties"].values())
    assert all(value == "" for value in example.values())
    assert all(placeholder.startswith("{{config.") for placeholder in
               (f"{{{{config.{key}}}}}" for key in placeholders.values()))
    assert fields["api_key"].sensitive is True
    assert fields["contact_email"].category is Category.EMAIL


def test_pipeline_replaces_values_without_changing_source_or_leaking_secret(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": "Contact user@company.ca for help.\n",
        "scripts/run.py": f'API_TOKEN = "{GENERIC_SECRET}"\nprint("ready")\n',
        "references/usage.md": "Keep this sentence.\n",
    })
    before = {path.relative_to(skill).as_posix(): path.read_bytes()
              for path in skill.rglob("*") if path.is_file()}
    output = tmp_path / "result"

    response = generalize_skill(skill, output_path=output)

    generated = output / "generalized-skill"
    assert "{{config.contact_email}}" in (generated / "SKILL.md").read_text()
    assert "{{config.api_token}}" in (generated / "scripts/run.py").read_text()
    assert "Keep this sentence." in (generated / "references/usage.md").read_text()
    schema = json.loads((output / "config.schema.json").read_text())
    example = json.loads((output / "config.example.json").read_text())
    report_text = (output / "findings.json").read_text()
    assert schema["required"] == ["api_token", "contact_email"]
    assert example == {"api_token": "", "contact_email": ""}
    assert_not_leaked([GENERIC_SECRET], report_text, json.dumps(schema), json.dumps(example))
    assert_not_leaked([GENERIC_SECRET], *(
        path.read_text(encoding="utf-8") for path in generated.rglob("*") if path.is_file()
    ))
    assert response["counts"]["placeholders"] == 2
    assert response["counts"]["residual_findings"] == 0
    assert response["unresolved"] == []
    after = {path.relative_to(skill).as_posix(): path.read_bytes()
             for path in skill.rglob("*") if path.is_file()}
    assert after == before


def test_pipeline_shares_same_value_and_purpose_placeholder(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": "Email user@company.ca.\n",
        "notes.txt": "Send a copy to user@company.ca.\n",
    })
    output = tmp_path / "result"
    response = generalize_skill(skill, output_path=output)

    assert response["counts"]["placeholders"] == 1
    assert "{{config.contact_email}}" in (output / "generalized-skill/SKILL.md").read_text()
    assert "{{config.contact_email}}" in (output / "generalized-skill/notes.txt").read_text()


def test_review_findings_stay_unchanged_and_can_be_approved(tmp_path):
    skill = write_skill(tmp_path / "skill", {"settings.yaml": "project_id: project-42\n"})
    result = scan_skill(skill)
    [finding] = result.findings
    assert finding.action.value == "review"

    review_output = tmp_path / "review"
    generalize_skill(skill, output_path=review_output)
    assert "project-42" in (review_output / "generalized-skill/settings.yaml").read_text()
    assert json.loads((review_output / "findings.json").read_text())["findings"][0]["status"] == "review"

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({finding.id: "approve"}), encoding="utf-8")
    approved_output = tmp_path / "approved"
    generalize_skill(skill, output_path=approved_output, decisions_path=decisions)
    assert "{{config.project_id}}" in (
        approved_output / "generalized-skill/settings.yaml").read_text()


def test_skipped_review_decision_keeps_value_and_reports_partial_status(tmp_path):
    skill = write_skill(tmp_path / "skill", {"settings.yaml": "project_id: project-42\n"})
    [finding] = scan_skill(skill).findings
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({finding.id: "skip"}), encoding="utf-8")
    output = tmp_path / "result"

    response = generalize_skill(skill, output_path=output, decisions_path=decisions)

    assert "project-42" in (
        output / "generalized-skill/settings.yaml").read_text(encoding="utf-8")
    assert response["status"] == "partial"
    assert response["unresolved"][0]["decision"] == "skip"
    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert report["findings"][0]["action"] == "keep"
    assert report["findings"][0]["status"] == "skipped"


def test_pipeline_rejects_output_inside_source_and_existing_output(tmp_path):
    skill = write_skill(tmp_path / "skill", {"SKILL.md": "No private values here."})
    with pytest.raises(ValueError, match="outside"):
        generalize_skill(skill, output_path=skill / "out")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        generalize_skill(skill, output_path=existing)


def test_pipeline_applies_exact_spans_and_preserves_environment_interfaces(tmp_path):
    original = (
        "Before ram.tati@company.ca, middle sohan.tati@company.ca, after. "
        "Read os.environ['SERVICE_KEY'] and ${DEPLOY_REGION}.\n"
    )
    skill = write_skill(tmp_path / "skill", {"SKILL.md": original})
    output = tmp_path / "result"

    response = generalize_skill(skill, output_path=output)

    transformed = (output / "generalized-skill/SKILL.md").read_text(encoding="utf-8")
    assert transformed == (
        "Before {{config.contact_email}}, middle {{config.contact_email_2}}, after. "
        "Read os.environ['SERVICE_KEY'] and ${DEPLOY_REGION}.\n"
    )
    assert response["counts"]["placeholders"] == 2
    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    kept = [row for row in report["findings"] if row["category"] == "environment_variable"]
    assert len(kept) == 2
    assert all(row["action"] == "keep" and row["status"] == "kept" for row in kept)


def test_repeated_values_with_different_purposes_get_distinct_fields(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "settings.yaml": "project_id: shared-42\naccount_id: shared-42\n"
    })
    output = tmp_path / "result"
    review_findings = [finding for finding in scan_skill(skill).findings
                       if finding.action.value == "review"]
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({finding.id: "approve" for finding in review_findings}),
                         encoding="utf-8")

    generalize_skill(skill, output_path=output, decisions_path=decisions)

    text = (output / "generalized-skill/settings.yaml").read_text(encoding="utf-8")
    assert "project_id: {{config.project_id}}" in text
    assert "account_id: {{config.account_id}}" in text
    example = json.loads((output / "config.example.json").read_text(encoding="utf-8"))
    assert example == {"account_id": "", "project_id": ""}


def test_report_has_safe_review_details_and_residuals(tmp_path):
    source_value = "project-42-private"
    skill = write_skill(tmp_path / "skill", {
        "settings.yaml": f"project_id: {source_value}\n"
    })
    output = tmp_path / "result"

    response = generalize_skill(skill, output_path=output)

    report_text = (output / "findings.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert source_value not in report_text
    assert response["status"] == "partial"
    assert response["unresolved"]
    assert report["unresolved_review_findings"][0]["status"] == "review"
    assert report["residual_findings"]
    assert report["residual_findings"][0]["status"] == "residual"
    assert report["residual_findings"][0]["confidence"] == 0.6
    assert report["findings"][0]["action"] == "review"
    # The review value was left in on purpose, so it is not an unexpected residual.
    assert report["unexpected_residual_count"] == 0
    assert response["counts"]["unexpected_residual_findings"] == 0
    assert all(w.get("reason") != "residual_candidates" for w in response["warnings"])


def test_review_left_in_after_a_multiline_replacement_is_not_unexpected(tmp_path):
    key = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(["A" * 64] * 3) + "\n-----END RSA PRIVATE KEY-----"
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": f"{key}\n\nUpstream: https://github.com/pimalaya/himalaya\n"})
    response = generalize_skill(skill, output_path=tmp_path / "result")
    assert response["counts"]["replacements"] == 1  # the key block, now one line
    assert response["counts"]["unresolved_reviews"] == 1  # the repository URL, lines later
    assert response["counts"]["unexpected_residual_findings"] == 0


def test_a_residual_that_was_not_left_for_review_is_still_unexpected(tmp_path, monkeypatch):
    from metaskill.generalize import pipeline

    skill = write_skill(tmp_path / "skill", {"settings.yaml": "project_id: project-42-private\n"})
    real_replace = pipeline.replace_spans
    # The output gains a value the source scan never saw, as a broken transform might.
    monkeypatch.setattr(pipeline, "replace_spans",
                        lambda text, replacements: real_replace(text, replacements)
                        + "owner_email: priya.nair@acme-corp.com\n")
    response = pipeline.generalize_skill(skill, output_path=tmp_path / "result")
    assert response["counts"]["unexpected_residual_findings"] == 1
    assert {"reason": "residual_candidates", "count": 1} in response["warnings"]


def test_approved_values_that_remain_are_reported_without_values(tmp_path, monkeypatch):
    from metaskill.generalize import pipeline

    skill = write_skill(tmp_path / "skill", {
        "settings.json": '{"api_token": "' + GENERIC_SECRET + '"}\n'
    })
    monkeypatch.setattr(pipeline, "replace_spans", lambda text, replacements: text)
    output = tmp_path / "result"

    response = pipeline.generalize_skill(skill, output_path=output)

    report_text = (output / "findings.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert response["status"] == "partial"
    assert response["counts"]["approved_values_remaining"] == 1
    assert report["validation"]["approved_values_replaced"] is False
    assert len(report["approved_values_remaining"]) == 1
    assert_not_leaked([GENERIC_SECRET], report_text)


def test_unapproved_review_decisions_are_rejected(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "settings.yaml": "project_id: project-42\n",
        "secrets.json": '{"api_token": "' + GENERIC_SECRET + '"}\n',
    })
    findings = scan_skill(skill).findings
    review = next(item for item in findings if item.action.value == "review")
    replace = next(item for item in findings if item.action.value == "replace")

    for index, payload in enumerate((
        {"unknown-finding": "approve"},
        {review.id: "replace"},
        {replace.id: "skip"},
    )):
        decisions = tmp_path / f"decisions-{index}.json"
        decisions.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="decisions"):
            generalize_skill(skill, output_path=tmp_path / f"out-{index}",
                             decisions_path=decisions)


def test_malformed_decisions_error_does_not_echo_file_contents(tmp_path):
    secret_text = "sensitive-project-value"
    skill = write_skill(tmp_path / "skill", {"SKILL.md": "Safe instructions."})
    decisions = tmp_path / "decisions.json"
    decisions.write_text('{ "oops": "' + secret_text, encoding="utf-8")

    with pytest.raises(ValueError) as error:
        generalize_skill(skill, output_path=tmp_path / "result", decisions_path=decisions)
    assert secret_text not in str(error.value)


def test_unscanned_files_are_reported_but_never_copied(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": "A safe skill.\n",
        "assets/private.png": b"\x89PNG\x00not text",
        "LICENSE": "MIT\n",
    })
    output = tmp_path / "result"

    generalize_skill(skill, output_path=output)

    generated = output / "generalized-skill"
    assert sorted(item.relative_to(generated).as_posix()
                  for item in generated.rglob("*") if item.is_file()) == ["SKILL.md"]
    report = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    assert report["skipped_files"] == [
        {"path": "LICENSE", "reason": "unsupported"},
        {"path": "assets/private.png", "reason": "unsupported"},
    ]


def test_existing_undefined_placeholder_fails_output_validation(tmp_path):
    skill = write_skill(tmp_path / "skill", {
        "SKILL.md": "Use the existing {{config.unlisted_value}} setting.\n"
    })
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="placeholder without a config schema"):
        generalize_skill(skill, output_path=output)
    assert not output.exists()


def test_pipeline_rejects_symlink_output_path(tmp_path):
    skill = write_skill(tmp_path / "skill", {"SKILL.md": "Safe instructions."})
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    link_output = tmp_path / "linked-output"
    link_output.symlink_to(real_output, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        generalize_skill(skill, output_path=link_output)
    assert list(real_output.iterdir()) == []


# --- Extra detectors from add-on plugins (extras.py) --------------------------------

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
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")
    assert result["status"] == "ok"  # the email is replaced; nothing is left for review
    assert result["detectors"] == ["regex"]
    assert result["counts"]["files_scanned"] == 1
    assert result["counts"]["by_category"] == {"email": 1}
    assert "metaskill-laya" in result["note"] and "ask" in result["note"]
    assert EMAIL not in json.dumps(result)


def test_extra_runs_after_regex_and_skips_claimed_spans(skill, clean_registry):
    calls = []
    clean_registry["fake"] = fake_extra(calls=calls)
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")

    assert result["detectors"] == ["regex", "fake"]
    assert result["counts"]["by_category"] == {"email": 1, "personal_data": 1}
    assert result["counts"]["by_detector"] == {"fake": 1, "regex": 1}
    assert result["counts"]["by_action"]["review"] >= 1
    assert "note" not in result
    assert result["status"] == "partial"  # the extra's finding waits for a decision
    generalized = (skill.parent / "out" / "generalized-skill" / "SKILL.md").read_text()
    assert NAME in generalized and EMAIL not in generalized
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
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")

    assert result["status"] == "ok"
    assert result["detectors"] == ["regex"]
    assert result["counts"]["findings"] == 1
    assert result["warnings"] == ["fake detector failed (RuntimeError): restart Hermes"]
    assert EMAIL not in json.dumps(result)  # the message is not passed on, only the hint


def test_extra_stops_at_the_time_limit(skill, clean_registry, monkeypatch):
    calls = []
    clean_registry["fake"] = fake_extra(calls=calls)
    monkeypatch.setenv(extras.TIME_LIMIT_ENV, "0")
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")
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
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")
    assert result["status"] == "error"
    assert "over the limit" in result["error"]


def test_skipped_files_are_summarized(skill):
    (skill / "logo.png").write_bytes(b"\x89PNG")
    result = pipeline.generalize_skill(str(skill), output_path=skill.parent / "out")
    assert result["counts"]["files_skipped"] == 1
    assert {"file": "logo.png", "reason": "unsupported"} in result["warnings"]


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


def test_tool_routes_generalize_to_the_pipeline(skill, clean_registry, monkeypatch):
    monkeypatch.chdir(skill.parent)  # the default output path is under the cwd
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": str(skill)}))
    assert result["success"] is True
    assert result["status"] == "ok"
    assert len(result["artifacts"]) == 4
    assert result["operation"] == "generalize"
    assert result["detectors"] == ["regex"]
    assert result["counts"]["findings"] == 1
