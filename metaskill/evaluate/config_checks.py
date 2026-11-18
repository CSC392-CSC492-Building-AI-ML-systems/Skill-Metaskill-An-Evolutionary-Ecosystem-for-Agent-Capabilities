"""Static Configuration Cleanliness checks over explicitly supplied evidence."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import Dimension, Finding, Severity


@dataclass(frozen=True)
class FixedSettingEvidence:
    """Reviewed instruction that fixes a setting known to vary across profiles.

    The caller establishes the meaning of the instruction and the variability
    requirement. The checker verifies that the reviewed line is still present.
    """

    setting: str
    line: str


def check_configuration(
    *,
    schema: Mapping[str, object] | None = None,
    skill_text: str = "",
    fixed_settings: Iterable[FixedSettingEvidence] = (),
    consumed_settings: frozenset[str] | None = None,
) -> list[Finding]:
    """Return confirmed findings for supported evidence, without reading files.

    Schema coverage is limited to explicitly declared top-level properties.
    Absent or unsupported evidence produces no finding for that check.
    consumed_settings must be a complete inventory of settings used by the
    skill. None means usage is unknown. An empty inventory confirms no usage.
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
                    location=f"config.schema.json#/properties/{pointer}",
                    message="Required configuration property has no consumer in the supplied usage inventory.",
                ))
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
                location=f"config.schema.json#/properties/{pointer}",
                message="Required configuration property has no nonblank description.",
            ))

    line_numbers = {}
    for number, line in enumerate(skill_text.splitlines(), start=1):
        line_numbers.setdefault(line.strip(), []).append(number)
    for evidence in dict.fromkeys(fixed_settings):
        instruction = evidence.line.strip()
        if not instruction or not evidence.setting.strip():
            continue
        for number in line_numbers.get(instruction, ()):
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="separation",
                severity=Severity.LOW,
                source="config-checks",
                rule="fixed-profile-setting",
                quote="",
                location=f"SKILL.md:{number}",
                message="Reviewed instruction fixes a setting that must vary across profiles.",
            ))
    return findings
