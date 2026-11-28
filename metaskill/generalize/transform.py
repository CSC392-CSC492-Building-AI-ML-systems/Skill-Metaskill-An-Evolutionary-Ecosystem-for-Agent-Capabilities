"""Exact, span-scoped replacements for generalization."""

from __future__ import annotations

from collections.abc import Iterable

from .detect import Finding


def replace_spans(text: str, replacements: Iterable[tuple[Finding, str]]) -> str:
    """Replace non-overlapping spans from right to left to preserve offsets."""
    ordered = sorted(replacements, key=lambda item: (item[0].start, item[0].end))
    previous_end = -1

    for finding, _replacement in ordered:
        if finding.start < 0 or finding.end < finding.start or finding.end > len(text):
            raise ValueError(f"invalid finding span: {finding.id}")
        if finding.start < previous_end:
            raise ValueError("replacement spans overlap")
        previous_end = finding.end

    for finding, replacement in reversed(ordered):
        text = text[:finding.start] + replacement + text[finding.end:]

    return text
