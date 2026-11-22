"""Stable placeholder naming and JSON configuration template generation.

Placeholders use ``{{config.<key>}}``. Generated examples contain blank values only;
source values are kept in memory while the pipeline runs and never enter artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .detect import Category, Finding

PLACEHOLDER_PATTERN = re.compile(r"\{\{config\.([a-z][a-z0-9_]*)\}\}")
_KEY_WORDS = re.compile(r"[^a-zA-Z0-9]+")


@dataclass(frozen=True)
class ConfigField:
    name: str
    category: Category
    sensitive: bool

    def to_schema(self) -> dict:
        description = {
            Category.EMAIL: "Email address used by the generalized skill.",
            Category.PHONE: "Phone number used by the generalized skill.",
            Category.CREDENTIAL: "Secret supplied by the user when personalizing the skill.",
            Category.FILE_PATH: "Machine-specific path required by the skill.",
            Category.REPOSITORY: "Repository URL or identifier required by the skill.",
            Category.ORGANIZATION_HOST: "Organization-specific hostname required by the skill.",
            Category.CONFIGURATION: "Configuration value required by the skill.",
            Category.PERSONAL_DATA: "Personal information supplied by the user when personalizing the skill.",
        }.get(self.category, "Configuration value required by the skill.")
        field = {
            "name": self.name,
            "type": "string",
            "required": True,
            "description": description,
            "validation": {},
            "sensitive": self.sensitive,
        }
        if self.category is Category.EMAIL:
            field["format"] = "email"
            field["validation"] = {"format": "email"}
        elif self.category is Category.REPOSITORY:
            field["format"] = "uri"
            field["validation"] = {"format": "uri"}
        if self.sensitive:
            field["writeOnly"] = True
        return field


def _normalize_name(value: str) -> str:
    """Convert a source assignment/key into a safe, stable config identifier."""
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    value = _KEY_WORDS.sub("_", value).strip("_").lower()
    if not value or not value[0].isalpha():
        value = f"setting_{value}" if value else "setting"
    return value[:64].rstrip("_") or "setting"


def _base_name(finding: Finding) -> str:
    if finding.name:
        return _normalize_name(finding.name)
    return {
        Category.EMAIL: "contact_email",
        Category.PHONE: "contact_phone",
        Category.CREDENTIAL: "api_token",
        Category.FILE_PATH: "workspace_path",
        Category.REPOSITORY: "repository_url",
        Category.ORGANIZATION_HOST: "organization_host",
        Category.CONFIGURATION: "setting",
        Category.PERSONAL_DATA: "personal_data",
    }.get(finding.category, "setting")


def assign_config_fields(
    replacements: Iterable[tuple[Finding, str]],
    *,
    reserved_names: Iterable[str] = (),
) -> tuple[dict[str, ConfigField], dict[str, str]]:
    """Assign reusable keys, deduplicating identical values with the same purpose.

    The second mapping is finding ID to placeholder. Raw values are only compared in
    memory and are not returned or stored in a field definition.
    """
    fields: dict[str, ConfigField] = {}
    finding_placeholders: dict[str, str] = {}
    shared: dict[tuple[Category, str, str], str] = {}
    occupied: dict[str, tuple[Category, str, str] | None] = {
        name: None for name in reserved_names
    }
    for finding, raw_value in replacements:
        base = _base_name(finding)
        fingerprint = (finding.category, raw_value, base)
        if fingerprint in shared:
            finding_placeholders[finding.id] = shared[fingerprint]
            continue
        name = base
        suffix = 2
        while name in occupied:
            name = f"{base[:58]}_{suffix}"
            suffix += 1
        occupied[name] = fingerprint
        shared[fingerprint] = name
        fields[name] = ConfigField(
            name=name,
            category=finding.category,
            sensitive=finding.sensitive,
        )
        finding_placeholders[finding.id] = name
    return fields, finding_placeholders


def build_config_template(fields: dict[str, ConfigField]) -> tuple[dict, dict[str, str]]:
    """Return a JSON Schema and blank example config for the generated placeholders."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Metaskill configuration",
        "type": "object",
        "properties": {name: field.to_schema() for name, field in sorted(fields.items())},
        "required": sorted(fields),
        "additionalProperties": False,
    }
    example = {name: "" for name in sorted(fields)}
    return schema, example
