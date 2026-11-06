"""Interface for the optional semantic stage that labels deterministic findings.

No provider ships here (design.md, section 3.2 step 3). A provider adapter implements
Classifier; classify() decides what it may see and checks what it returns.

What a classifier sees:
- Credentials are never sent, as candidates or as context. The detector already knows
  they must be replaced, and redact() removes them from every context window.
- Environment variable references are not sent either; they are kept as they are.
- Each request carries the candidate value and a few lines around it, never whole files.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .detect import Category, Finding, ScanResult, redact

CLASSIFIED_CATEGORIES = frozenset({
    Category.EMAIL, Category.PHONE, Category.FILE_PATH, Category.REPOSITORY,
    Category.ORGANIZATION_HOST, Category.CONFIGURATION,
})
_CONFIG_KEY = re.compile(r"[a-z][a-z0-9_.]{0,63}")


class Label(StrEnum):
    PERSONAL = "personal"  # identifies the author or their machine
    ORGANIZATION = "organization"  # specific to the author's team, company or project
    REUSABLE = "reusable"  # part of the skill's logic or public documentation
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ClassificationRequest:
    finding_id: str
    file: str
    line: int
    category: Category
    value: str
    name: str  # variable or key the value is assigned to, if any
    context: str  # nearby lines, with credentials redacted


@dataclass(frozen=True)
class Classification:
    finding_id: str
    label: Label
    replace: bool = False
    config_key: str = ""  # suggested configuration key, e.g. "contact_email"


class Classifier(Protocol):
    def classify(self, requests: list[ClassificationRequest]) -> list[Classification]: ...


def build_requests(result: ScanResult, context_lines: int = 2) -> list[ClassificationRequest]:
    by_file: dict[str, list[Finding]] = {}
    for finding in result.findings:
        by_file.setdefault(finding.file, []).append(finding)

    requests = []
    for source in result.files:
        findings = by_file.get(source.path, [])
        lines = redact(source.text, findings).split("\n")
        for finding in findings:
            if finding.category not in CLASSIFIED_CATEGORIES:
                continue
            first = max(finding.line - 1 - context_lines, 0)
            last = finding.line + context_lines
            requests.append(ClassificationRequest(
                finding_id=finding.id,
                file=finding.file,
                line=finding.line,
                category=finding.category,
                value=finding.value,
                name=finding.name,
                context="\n".join(lines[first:last]),
            ))
    return requests


def classify(result: ScanResult, classifier: Classifier,
             context_lines: int = 2) -> dict[str, Classification]:
    """Label every classifiable finding. Anything the classifier gets wrong is UNCERTAIN."""
    requests = build_requests(result, context_lines)
    if not requests:
        return {}
    labels = {r.finding_id: Classification(r.finding_id, Label.UNCERTAIN) for r in requests}
    for response in _as_list(classifier.classify(requests)):
        if not isinstance(response, Classification) or response.finding_id not in labels:
            continue
        try:
            label = Label(response.label)
        except ValueError:
            continue
        key = response.config_key if isinstance(response.config_key, str) else ""
        labels[response.finding_id] = Classification(
            finding_id=response.finding_id,
            label=label,
            replace=response.replace is True and label is not Label.UNCERTAIN,
            config_key=key if _CONFIG_KEY.fullmatch(key) else "",
        )
    return labels


def _as_list(responses: Iterable[Classification] | None) -> list[Classification]:
    try:
        return list(responses or [])
    except TypeError:
        return []
