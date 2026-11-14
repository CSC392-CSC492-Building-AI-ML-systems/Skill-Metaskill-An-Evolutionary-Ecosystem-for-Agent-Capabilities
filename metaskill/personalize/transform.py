"""Literal placeholder substitution (design.md, section 4 step 3)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..generalize.config import PLACEHOLDER_PATTERN


@dataclass
class Substitution:
    text: str
    resolved: Counter = field(default_factory=Counter)  # key -> occurrences replaced
    unresolved: list[tuple[str, int]] = field(default_factory=list)  # (key, 1-based line)


def substitute(text: str, values: dict[str, str]) -> Substitution:
    """Replace each {{config.<key>}} that has a value with that value, exactly as given.

    The replacement is a function, so a value is never read as a regex template, and the
    scan is one pass over the template, so placeholder text inside a value is not expanded.
    Placeholders without a value are left unchanged and listed with their line.
    """
    result = Substitution(text)
    line, scanned = 1, 0

    def replace(match):
        nonlocal line, scanned
        key = match.group(1)
        if key in values:
            result.resolved[key] += 1
            return values[key]
        line += text.count("\n", scanned, match.start())
        scanned = match.start()
        result.unresolved.append((key, line))
        return match.group(0)

    result.text = PLACEHOLDER_PATTERN.sub(replace, text)
    return result
