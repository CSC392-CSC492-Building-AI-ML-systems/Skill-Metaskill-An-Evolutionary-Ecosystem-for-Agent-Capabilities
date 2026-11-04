"""Placeholder naming and JSON configuration template generation.

Placeholders use ``{{config.<key>}}``. Generated examples contain blank
values only. Source values stay in memory and never enter these artifacts.
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
        descriptions = {
            Category.EMAIL: "Email address used by the generalized skill.",
            Category.PHONE: "Phone number used by the generalized skill.",
            Category.CREDENTIAL: "Secret supplied when personalizing the skill.",
            Category.FILE_PATH: "Machine-specific path required by the skill.",
            Category.REPOSITORY: "Repository URL or identifier required by the skill.",
            Category.ORGANIZATION_HOST: "Organization-specific hostname required by the skill.",
            Category.CONFIGURATION: "Configuration value required by the skill.",
        }
        field = {
            "name": self.name,
            "type": "string",
            "required": True,
            "description": descriptions.get(
                self.category, "Configuration value required by the skill."
            ),
            "validation": {},
            "sensitive": self.sensitive,
        }

        if self.category is Category.EMAIL:
            field["format"] = "email"
            field["validation"] = {"format": "email"}
        elif self.category is Category.REPOSITORY:
            field["format"] = "uri"
            field["validation"] = {"format": "uri"}
        elif self.category is Category.CREDENTIAL:
            field["writeOnly"] = True

        return field


def _normalize_name(value: str) -> str:
    """Convert an assignment name into a safe lowercase config key."""
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    value = _KEY_WORDS.sub("_", value).strip("_").lower()

    if not value or not value[0].isalpha():
        value = f"setting_{value}" if value else "setting"

    return value[:64].rstrip("_") or "setting"


def _base_name(finding: Finding) -> str:
    if finding.name:
        return _normalize_name(finding.name)

    defaults = {
        Category.EMAIL: "contact_email",
        Category.PHONE: "contact_phone",
        Category.CREDENTIAL: "api_token",
        Category.FILE_PATH: "workspace_path",
        Category.REPOSITORY: "repository_url",
        Category.ORGANIZATION_HOST: "organization_host",
        Category.CONFIGURATION: "setting",
    }
    return defaults.get(finding.category, "setting")


def assign_config_fields(
    replacements: Iterable[tuple[Finding, str]],
) -> tuple[dict[str, ConfigField], dict[str, str]]:
    """Create config fields and map each finding ID to its placeholder key.

    Repeated values with the same category and purpose share a key. Raw values
    are compared in memory only; this function does not return or store them.
    """
    fields: dict[str, ConfigField] = {}
    finding_placeholders: dict[str, str] = {}
    shared: dict[tuple[Category, str, str], str] = {}
    occupied: dict[str, tuple[Category, str, str]] = {}

    for finding, raw_value in replacements:
        base = _base_name(finding)
        fingerprint = (finding.category, raw_value, base)

        if fingerprint in shared:
            finding_placeholders[finding.id] = shared[fingerprint]
            continue

        name = base
        suffix = 2
        while name in occupied and occupied[name] != fingerprint:
            name = f"{base[:58]}_{suffix}"
            suffix += 1

        occupied[name] = fingerprint
        shared[fingerprint] = name
        fields[name] = ConfigField(
            name=name,
            category=finding.category,
            sensitive=finding.category is Category.CREDENTIAL,
        )
        finding_placeholders[finding.id] = name

    return fields, finding_placeholders


def build_config_template(fields: dict[str, ConfigField]) -> tuple[dict, dict[str, str]]:
    """Return a JSON Schema and a blank example config."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Metaskill configuration",
        "type": "object",
        "properties": {
            name: field.to_schema()
            for name, field in sorted(fields.items())
        },
        "required": sorted(fields),
        "additionalProperties": False,
    }
    example = {name: "" for name in sorted(fields)}
    return schema, example
