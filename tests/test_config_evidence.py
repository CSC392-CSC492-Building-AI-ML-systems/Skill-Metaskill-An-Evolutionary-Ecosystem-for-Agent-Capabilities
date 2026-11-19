from pathlib import Path

from metaskill.evaluate.config_evidence import collect_configuration_evidence
from metaskill.evaluate.config_cleanliness import score
from metaskill.evaluate.models import EvaluationConfig, Skill, Status

ROOT = Path(__file__).resolve().parents[1]


def skill_at(tmp_path, body, files=None):
    (tmp_path / "SKILL.md").write_text(body, encoding="utf-8")
    for name, text in (files or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Skill.load(tmp_path)


def test_template_references_use_existing_generalizer_syntax(tmp_path):
    skill = skill_at(tmp_path, "Use {{config.organization}}.")
    evidence = collect_configuration_evidence(skill)
    assert "organization" in evidence.referenced_settings
    assert not evidence.usage_complete
    [finding] = evidence.findings
    assert finding.rule == "template-setting-reference"
    assert finding.location == "SKILL.md:1"
    assert finding.quote == ""


def test_real_google_workspace_environment_reference():
    folder = ROOT / "tests/fixtures/hermes-skills/productivity/google-workspace"
    evidence = collect_configuration_evidence(Skill(
        path=folder, name=folder.name, body=(folder / "SKILL.md").read_text(encoding="utf-8"),
    ))
    assert any(
        f.rule == "environment-setting-reference"
        and f.location.startswith("scripts/google_api.py:")
        for f in evidence.findings
    )
    assert not evidence.usage_complete


def test_embedded_values_are_candidates_and_do_not_force_zero(tmp_path):
    skill = skill_at(tmp_path, "# Deploy", {
        "deploy.sh": 'BUILD_HOST="build.acme.internal"\n',
    })
    evidence = collect_configuration_evidence(skill)
    assert any(f.rule == "embedded-value-needs-review" for f in evidence.findings)
    result = score(skill, EvaluationConfig())
    assert result.value is None and result.status is Status.SKIPPED
    assert "build.acme.internal" not in str(result.to_dict())


def test_config_values_are_not_reported_as_embedded_instruction_defects(tmp_path):
    skill = skill_at(tmp_path, "# Configure this skill", {
        "config.json": '{"host":"build.acme.internal"}',
    })
    assert collect_configuration_evidence(skill).findings == []


def test_missing_references_do_not_prove_required_setting_is_unused(tmp_path):
    skill = skill_at(tmp_path, "# Skill", {
        "config.schema.json": '{"required":["organization"],"properties":{"organization":{"description":"Label"}}}',
        "worker.py": 'settings = load_config()\nuse(settings[name])\n',
    })
    result = score(skill, EvaluationConfig())
    assert result.value is None
    assert not any(f.rule == "unused-required-setting" for f in result.findings)


def test_existing_personalize_template_collects_references():
    folder = ROOT / "tests/fixtures/personalize/deploy-helper/generalized/generalized-skill"
    evidence = collect_configuration_evidence(Skill(
        path=folder, name=folder.name, body=(folder / "SKILL.md").read_text(encoding="utf-8"),
    ))
    assert {"workdir", "build_host", "contact_email"} <= evidence.referenced_settings
    assert not evidence.usage_complete


def test_skipped_sources_are_visible(tmp_path):
    skill = skill_at(tmp_path, "# Skill", {"data.bin": "opaque"})
    assert "1 skipped file(s)" in collect_configuration_evidence(skill).detail


def test_invalid_inventory_is_reported_without_values(tmp_path):
    skill = Skill(path=tmp_path / "missing", name="missing", body="")
    evidence = collect_configuration_evidence(skill)
    assert not evidence.findings and not evidence.usage_complete
    assert "unavailable" in evidence.detail


def test_normal_evaluation_includes_arbitrary_bundle_evidence(tmp_path):
    import json
    from metaskill.evaluate import evaluate_skill

    folder = tmp_path / "skill"
    folder.mkdir()
    skill_at(folder, "Run worker.py", {
        "worker.py": 'import os\nvalue = os.getenv("SERVICE_ENDPOINT")\n',
    })
    result = evaluate_skill(folder, output_path=tmp_path / "reports")
    report = next(Path(p) for p in result["artifacts"] if p.endswith(".json"))
    [entry] = json.loads(report.read_text(encoding="utf-8"))["skills"]
    assert entry["scores"]["config_cleanliness"] is None
    assert any(
        f["source"] == "config-evidence" and f["rule"] == "environment-setting-reference"
        for f in entry["findings"]
    )
    assert "Usage is partial" in entry["dimensions"]["config_cleanliness"]["detail"]


def test_evidence_cap_preserves_partial_coverage(tmp_path, monkeypatch):
    import metaskill.evaluate.config_evidence as adapter

    monkeypatch.setattr(adapter, "MAX_EVIDENCE", 1)
    evidence = collect_configuration_evidence(skill_at(
        tmp_path, "{{config.first}} {{config.second}}",
    ))
    assert len(evidence.findings) == 1
    assert not evidence.usage_complete
    assert "Evidence limit reached" in evidence.detail
