"""Bounded configuration evidence from arbitrary skill bundles.

Generalization's inventory and detectors supply source locations and candidate
values. Values remain in memory and never enter Configuration Cleanliness reports.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..generalize.config import PLACEHOLDER_PATTERN
from ..generalize.detect import Action, Category, detect_file
from ..generalize.inventory import InventoryError, load_inventory
from ..links import through_link
from .models import Dimension, Finding, Severity, Skill

MAX_EVIDENCE = 1000
# The generalization detector is eager and can be quadratic within a file.
# Bound its inputs before calling it, independently of the output-count cap.
MAX_ANALYSIS_FILE_BYTES = 64 * 1024
MAX_ANALYSIS_TOTAL_BYTES = 256 * 1024
# Configuration data and example values are inputs, rather than evidence of
# hardcoding in the executable instructions.
INSTRUCTION_SUFFIXES = frozenset({".md", ".txt", ".py", ".sh", ".js", ".ts"})
REFERENCE_SOURCE = "config-evidence"


@dataclass
class ConfigurationEvidence:
    findings: list[Finding] = field(default_factory=list)
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
    limited = []
    remaining = MAX_ANALYSIS_TOTAL_BYTES
    for source in inventory.files:
        if Path(source.path).suffix.lower() not in INSTRUCTION_SUFFIXES:
            continue
        # The inventory walks into Windows junctions, so a file may be outside the skill.
        if Path(skill.path).is_dir() and through_link(skill.path, Path(skill.path) / source.path):
            limited.append(source.path)
            continue
        size = len(source.text.encode("utf-8"))
        if size > MAX_ANALYSIS_FILE_BYTES:
            limited.append(source.path)
            continue
        if size > remaining:
            limited.append(source.path)
            continue
        remaining -= size
        line, cursor = 1, 0
        for match in PLACEHOLDER_PATTERN.finditer(source.text):
            line += source.text.count("\n", cursor, match.start())
            cursor = match.start()
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
                return _result(findings, len(inventory.skipped), limited, capped=True)

        for detected in detect_file(source):
            if detected.category is Category.ENVIRONMENT_VARIABLE:
                rule = "environment-setting-reference"
                category = "configuration-reference"
                message = "Generalization detected an external environment setting reference."
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
                return _result(findings, len(inventory.skipped), limited, capped=True)

    return _result(findings, len(inventory.skipped), limited)


def _result(findings, skipped, limited, *, capped=False) -> ConfigurationEvidence:
    detail = (
        f"Configuration evidence from the generalization inventory and detectors: "
        f"{len(findings)} reference(s) or review candidate(s); {skipped} skipped file(s). "
        "Usage is partial; references do not establish complete consumption or required variability."
    )
    if limited:
        detail += (
            f" {len(limited)} file(s) skipped by evidence processing limits "
            f"({MAX_ANALYSIS_FILE_BYTES} bytes per file, "
            f"{MAX_ANALYSIS_TOTAL_BYTES} bytes total)."
        )
    if capped:
        detail += " Evidence limit reached."
    return ConfigurationEvidence(findings, detail)
