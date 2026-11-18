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
    assert check_configuration(schema=schema("organization"), consumed_settings=consumed) == []
    [finding] = check_configuration(
        schema=schema("organization", "legacy_region"), consumed_settings=consumed,
    )
    assert finding.category == "necessary-setup"
    assert finding.rule == "unused-required-setting"
    assert finding.location == "config.schema.json#/properties/legacy_region"
    assert check_configuration(schema=schema("organization", "legacy_region")) == []

    [finding] = check_configuration(
        schema=schema("organization"), consumed_settings=frozenset(),
    )
    assert finding.location == "config.schema.json#/properties/organization"
