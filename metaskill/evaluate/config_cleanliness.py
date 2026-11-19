"""Report deterministic Configuration Cleanliness evidence with partial coverage."""

import json
from collections.abc import Iterable

from .config_checks import FixedSettingEvidence, check_configuration
from .golden.config_evidence import configuration_evidence, reference_usage
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
    consumed_settings: frozenset[str] | None = None,
    usage_complete: bool = False,
) -> DimensionScore:
    """Report available findings and apply the confirmed Separation gate."""
    schema, schema_detail = _schema(skill)
    pilot_fixed, unused = configuration_evidence(skill)
    fixed_settings = (*fixed_settings, *pilot_fixed)
    reviewed_usage = reference_usage(skill)
    if consumed_settings is None:
        consumed_settings = reviewed_usage
        usage_complete = reviewed_usage is not None
    if not fixed_settings and reviewed_usage is None:
        schema_detail += " No reviewed profile-variability evidence supplied."
    if consumed_settings is None:
        schema_detail += " Necessary Setup needs a complete usage inventory."
    findings = check_configuration(
        schema=schema, skill_text=skill.body, fixed_settings=fixed_settings,
        consumed_settings=consumed_settings, usage_complete=usage_complete,
    )
    properties = schema.get("properties", {}) if schema is not None else {}
    for name in unused:
        if name in properties:
            pointer = name.replace("~", "~0").replace("/", "~1")
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="integrity",
                severity=Severity.LOW,
                source="config-checks",
                rule="unused-pilot-setting",
                quote="",
                location=f"{SCHEMA_NAME}#/properties/{pointer}",
                message="The instruction-only pilot declares a setting with no consumer.",
            ))
    # A confirmed portability failure fixes the maximum score at zero,
    # even when the remaining properties have incomplete evidence.
    if any(f.rule == "fixed-profile-setting" for f in findings):
        return DimensionScore(
            dimension=Dimension.CONFIG_CLEANLINESS,
            value=0.0,
            status=Status.OK,
            reason="Confirmed Separation failure: the skill cannot adapt across intended profiles.",
            detail=schema_detail,
            findings=findings,
        )
    if schema is not None and reviewed_usage is not None and not findings:
        return DimensionScore(
            dimension=Dimension.CONFIG_CLEANLINESS,
            value=1.0,
            status=Status.OK,
            reason="All four Configuration Cleanliness properties pass for the reviewed reference.",
            detail="The required organization is documented, consumed, and supplied through configuration.",
        )
    return DimensionScore(
        dimension=Dimension.CONFIG_CLEANLINESS,
        value=None,
        status=Status.SKIPPED,
        reason="Configuration Cleanliness score unavailable: incomplete coverage.",
        detail=schema_detail,
        findings=findings,
    )
