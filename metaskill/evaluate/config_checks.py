"""Static Configuration Cleanliness checks over explicitly supplied evidence."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import Dimension, Finding, Severity


@dataclass(frozen=True)
class FixedSettingEvidence:
    """Instruction annotated by an evaluator or a reviewed fixture manifest.

    The caller establishes the setting's required variability and supplies a
    reviewed instruction. Matching verifies that the instruction remains present.
    MET-56 supplies evidence for the pinned pilot bundles.
    """

    setting: str
    line: str


def _instruction(line: str) -> str:
    return re.sub(r"^(?:[-+*]|[0-9]+[.)])[ \t]+", "", line.strip())


def check_configuration(
    *,
    schema: Mapping[str, object] | None = None,
    schema_location: str = "config.schema.json",
    skill_text: str = "",
    fixed_settings: Iterable[FixedSettingEvidence] = (),
    consumed_settings: frozenset[str] | None = None,
) -> list[Finding]:
    """Check explicit top-level properties and reviewed fixed-setting evidence.

    Schema coverage is limited to explicitly declared top-level properties.
    Absent or unsupported evidence produces no finding for that check.
    consumed_settings must be a complete inventory of settings used by the
    skill. None means usage is unknown. An empty inventory confirms no usage.

    References and compositions are unsupported and skipped for description
    checks. A required name without a local property definition may be supplied
    elsewhere in a valid schema, so it is also left unassessed.
    """
    findings = []
    required = schema.get("required") if schema is not None else None
    properties = schema.get("properties") if schema is not None else None
    if isinstance(required, list) and isinstance(properties, Mapping):
        for name in dict.fromkeys(name for name in required if isinstance(name, str)):
            definition = properties.get(name)
            if not isinstance(definition, Mapping):
                continue
            pointer = name.replace("~", "~0").replace("/", "~1")
            if consumed_settings is not None and name not in consumed_settings:
                findings.append(Finding(
                    dimension=Dimension.CONFIG_CLEANLINESS,
                    category="necessary-setup",
                    severity=Severity.LOW,
                    source="config-checks",
                    rule="unused-required-setting",
                    quote="",
                    location=f"{schema_location}#/properties/{pointer}",
                    message="Required configuration property has no consumer in the supplied usage inventory.",
                ))

            if any(key in definition for key in ("$ref", "allOf", "oneOf", "anyOf")):
                continue
            description = definition.get("description")
            if isinstance(description, str) and description.strip():
                continue
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="documentation",
                severity=Severity.LOW,
                source="config-checks",
                rule="missing-required-description",
                quote="",
                location=f"{schema_location}#/properties/{pointer}",
                message="Required configuration property has no nonblank description.",
            ))

    line_numbers = {}
    for number, line in enumerate(skill_text.splitlines(), start=1):
        line_numbers.setdefault(_instruction(line), []).append(number)
    for evidence in dict.fromkeys(fixed_settings):
        instruction = _instruction(evidence.line)
        if not instruction or not evidence.setting.strip():
            continue
        for number in line_numbers.get(instruction, ()):
            # A property pointer identifies the reviewed setting without exposing
            # its configured value.
            pointer = evidence.setting.replace("~", "~0").replace("/", "~1")
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="separation",
                severity=Severity.LOW,
                source="config-checks",
                rule="fixed-profile-setting",
                quote="",
                location=f"SKILL.md:{number}",
                message=f"Reviewed instruction fixes setting /{pointer}, which must vary across profiles.",
            ))
    return findings
