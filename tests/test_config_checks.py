import json
from pathlib import Path

from metaskill.evaluate.config_checks import FixedSettingEvidence, check_configuration


PILOT = (
    Path(__file__).resolve().parents[1]
    / "metaskill/evaluate/golden/synthetic/ticket-summary"
)
FIXED_ORGANIZATION = FixedSettingEvidence(
    "organization",
    'Always use organization "Example Alpha". No external configuration is read.',
)


def test_pilot_required_description():
    def schema(variant):
        path = PILOT / "skills" / variant / "config.schema.json"
        return json.loads(path.read_text(encoding="utf-8"))

    assert check_configuration(schema=schema("reference")) == []
    [finding] = check_configuration(schema=schema("undocumented"))
    assert finding.rule == "missing-required-description"
    assert finding.location == "config.schema.json#/properties/organization"


def test_required_description_checks_blank_text_and_escapes_pointer():
    schema = {
        "required": ["a~/b", "a~/b"],
        "properties": {"a~/b": {"description": " "}, "optional": {}},
    }
    [finding] = check_configuration(schema=schema)
    assert finding.location == "config.schema.json#/properties/a~0~1b"


def test_pilot_fixed_profile_setting_requires_matching_reviewed_instruction():
    text = (PILOT / "skills/hardcoded/SKILL.md").read_text(encoding="utf-8")
    assert check_configuration(skill_text=text) == []
    [finding] = check_configuration(skill_text=text, fixed_settings=[FIXED_ORGANIZATION])
    assert finding.rule == "fixed-profile-setting"
    assert finding.location == "SKILL.md:7"
    assert finding.quote == ""
    assert "Example Alpha" not in finding.message


def test_benign_example_does_not_match_the_reviewed_instruction():
    text = 'An example organization label is "Example Alpha".'
    assert check_configuration(skill_text=text, fixed_settings=[FIXED_ORGANIZATION]) == []


def test_necessary_setup_golden_cases():
    def schema(*required):
        return {
            "required": list(required),
            "properties": {
                "organization": {"description": "Organization label in the output."},
                "legacy_region": {"description": "Legacy region label."},
            },
        }

    consumed = frozenset({"organization"})
    assert check_configuration(schema=schema("organization"), usage_complete=True, consumed_settings=consumed) == []
    [finding] = check_configuration(
        schema=schema("organization", "legacy_region"), usage_complete=True, consumed_settings=consumed,
    )
    assert finding.category == "necessary-setup"
    assert finding.rule == "unused-required-setting"
    assert finding.location == "config.schema.json#/properties/legacy_region"
    assert check_configuration(schema=schema("organization", "legacy_region")) == []

    [finding] = check_configuration(
        schema=schema("organization"), usage_complete=True, consumed_settings=frozenset(),
    )
    assert finding.location == "config.schema.json#/properties/organization"

def test_indirect_descriptions_are_left_unassessed():
    for keyword in ("$ref", "allOf", "oneOf", "anyOf"):
        definition = {"$ref": "#/$defs/label"} if keyword == "$ref" else {
            keyword: [{"description": "Label"}],
        }
        assert check_configuration(schema={
            "required": ["label"],
            "properties": {"label": definition},
            "$defs": {"label": {"description": "Label"}},
        }) == []


def test_required_name_without_local_definition_is_left_unassessed():
    assert check_configuration(schema={
        "required": ["label"], "properties": {},
    }) == []


def test_custom_schema_location():
    [finding] = check_configuration(schema={
        "required": ["label"], "properties": {"label": {}},
    }, schema_location="settings/schema.json")
    assert finding.location == "settings/schema.json#/properties/label"


def test_markdown_bullets_and_numbered_instructions_match():
    for prefix in ("- ", "* ", "+ ", "1. ", "2) "):
        [finding] = check_configuration(
            skill_text=prefix + FIXED_ORGANIZATION.line,
            fixed_settings=[FIXED_ORGANIZATION],
        )
        assert finding.location == "SKILL.md:1"
        assert "/organization" in finding.message


def test_distinct_settings_on_same_line_have_distinct_messages():
    findings = check_configuration(
        skill_text="Use fixed settings.",
        fixed_settings=[
            FixedSettingEvidence("organization", "Use fixed settings."),
            FixedSettingEvidence("region", "Use fixed settings."),
        ],
    )
    assert len(findings) == 2
    assert findings[0].message != findings[1].message


def test_partial_usage_inventory_cannot_prove_a_setting_unused():
    schema = {"required": ["organization"], "properties": {"organization": {
        "description": "Organization label",
    }}}
    assert check_configuration(schema=schema, consumed_settings=frozenset()) == []


def test_complete_usage_claim_requires_an_inventory():
    import pytest

    with pytest.raises(ValueError, match="requires an inventory"):
        check_configuration(usage_complete=True)
