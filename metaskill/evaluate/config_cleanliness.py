"""Report deterministic Configuration Cleanliness evidence with partial coverage."""

import json
from collections.abc import Iterable

from .config_checks import FixedSettingEvidence, check_configuration
from .golden.config_evidence import configuration_evidence
from .models import Dimension, DimensionScore, EvaluationConfig, Finding, Severity, Skill, Status

SCHEMA_NAME = "config.schema.json"
MAX_SCHEMA_BYTES = 256 * 1024


def _schema(skill: Skill) -> tuple[dict | None, str]:
    path = skill.path / SCHEMA_NAME
    try:
        if path.is_symlink():
            return None, "Schema is a symlink."
        if not path.is_file():
            return None, "No bundled configuration schema."
        with path.open("rb") as stream:
            raw = stream.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            return None, "Schema exceeds the size limit."
        schema = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None, "Bundled configuration schema could not be read as JSON."
    if not isinstance(schema, dict):
        return None, "Bundled configuration schema is not a JSON object."
    if not isinstance(schema.get("required"), list) or not isinstance(schema.get("properties"), dict):
        return None, "Schema does not declare top-level required properties."
    return schema, "Checked descriptions on declared top-level required properties."


def score(
    skill: Skill,
    config: EvaluationConfig,
    llm: object = None,
    *,
    fixed_settings: Iterable[FixedSettingEvidence] = (),
) -> DimensionScore:
    """Return findings for available evidence and leave the numeric score unset."""
    schema, schema_detail = _schema(skill)
    pilot_fixed, unused = configuration_evidence(skill)
    fixed_settings = (*fixed_settings, *pilot_fixed)
    if not fixed_settings:
        schema_detail += " No reviewed profile-variability evidence supplied."
    findings = check_configuration(
        schema=schema, skill_text=skill.body, fixed_settings=fixed_settings,
    )
    properties = schema.get("properties", {}) if schema is not None else {}
    for name in unused:
        if name in properties:
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="integrity",
                severity=Severity.LOW,
                source="config-checks",
                rule="unused-pilot-setting",
                quote="",
                location=f"{SCHEMA_NAME}#/properties/{name}",
                message="The instruction-only pilot declares a setting with no consumer.",
            ))
    return DimensionScore(
        dimension=Dimension.CONFIG_CLEANLINESS,
        value=None,
        status=Status.SKIPPED,
        reason="Configuration Cleanliness score unavailable: incomplete coverage.",
        detail=schema_detail,
        findings=findings,
    )
