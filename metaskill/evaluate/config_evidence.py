"""Bounded configuration evidence from arbitrary skill bundles.

Generalization's inventory and detectors supply source locations and candidate
values. Values remain in memory and never enter Configuration Cleanliness reports.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..generalize.config import PLACEHOLDER_PATTERN
from ..generalize.detect import Action, Category, detect_file
from ..generalize.inventory import InventoryError, load_inventory
from .models import Dimension, Finding, Severity, Skill

MAX_EVIDENCE = 1000
# Configuration data and example values are inputs, rather than evidence of
# hardcoding in the executable instructions.
INSTRUCTION_SUFFIXES = frozenset({".md", ".txt", ".py", ".sh", ".js", ".ts"})
REFERENCE_SOURCE = "config-evidence"


@dataclass
class ConfigurationEvidence:
    findings: list[Finding] = field(default_factory=list)
    referenced_settings: frozenset[str] = frozenset()
    usage_complete: bool = False
    detail: str = ""


def collect_configuration_evidence(skill: Skill) -> ConfigurationEvidence:
    """Collect references and review candidates; semantic usage stays incomplete.

    A literal reference proves that the name occurs in the bundle. It cannot prove
    execution, required variability, or that other settings are unused.
    """
    try:
        inventory = load_inventory(skill.path)
    except (InventoryError, OSError, ValueError):
        return ConfigurationEvidence(detail="Configuration evidence inventory unavailable.")

    findings = []
    referenced = set()
    for source in inventory.files:
        if Path(source.path).suffix.lower() not in INSTRUCTION_SUFFIXES:
            continue
        line, cursor = 1, 0
        for match in PLACEHOLDER_PATTERN.finditer(source.text):
            line += source.text.count("\n", cursor, match.start())
            cursor = match.start()
            referenced.add(match.group(1))
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category="configuration-reference",
                severity=Severity.INFO,
                source=REFERENCE_SOURCE,
                rule="template-setting-reference",
                quote="",
                location=f"{source.path}:{line}",
                message="Metaskill configuration placeholder occurs here.",
            ))
            if len(findings) >= MAX_EVIDENCE:
                return _result(findings, referenced, len(inventory.skipped), capped=True)

        for detected in detect_file(source):
            if detected.category is Category.ENVIRONMENT_VARIABLE:
                rule = "environment-setting-reference"
                category = "configuration-reference"
                message = "Generalization detected an external environment setting reference."
                # Never echo detected values, including environment names. Only use
                # syntactically valid names internally for reference coverage.
                if detected.name and detected.name.isidentifier():
                    referenced.add(detected.name)
            elif detected.action in (Action.REPLACE, Action.REVIEW):
                rule = "embedded-value-needs-review"
                category = "separation-review"
                message = (
                    "Generalization detected an embedded value. Review whether this "
                    "value must vary across intended profiles before scoring Separation."
                )
            else:
                continue
            findings.append(Finding(
                dimension=Dimension.CONFIG_CLEANLINESS,
                category=category,
                severity=Severity.INFO,
                source=REFERENCE_SOURCE,
                rule=rule,
                quote="",
                location=f"{source.path}:{detected.line}",
                message=message,
            ))
            if len(findings) >= MAX_EVIDENCE:
                return _result(findings, referenced, len(inventory.skipped), capped=True)

    return _result(findings, referenced, len(inventory.skipped))


def _result(findings, referenced, skipped, *, capped=False) -> ConfigurationEvidence:
    detail = (
        f"Configuration evidence from the generalization inventory and detectors: "
        f"{len(findings)} reference(s) or review candidate(s); {skipped} skipped file(s). "
        "Usage is partial; references do not establish complete consumption or required variability."
    )
    if capped:
        detail += " Evidence limit reached."
    return ConfigurationEvidence(findings, frozenset(referenced), False, detail)
