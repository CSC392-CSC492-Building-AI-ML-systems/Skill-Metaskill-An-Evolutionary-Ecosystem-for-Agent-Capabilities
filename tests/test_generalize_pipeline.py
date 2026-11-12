"""End-to-end coverage for config templates and the generalization pipeline."""

import json

import pytest

from skill_fixtures.secrets import GENERIC_SECRET, assert_not_leaked
from metaskill.generalize.config import assign_config_fields, build_config_template
from metaskill.generalize.detect import Category, scan_skill
from metaskill.generalize.pipeline import generalize_skill


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
