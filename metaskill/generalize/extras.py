"""Extra detectors supplied by optional add-on plugins, such as metaskill-laya.

Generalization works without any of them. An add-on adds detection that the regex
layer cannot do, at a cost (a model download, minutes of CPU time), so the user
installs it only when they want it.

An add-on is a separate Hermes plugin. It cannot import metaskill, and Hermes gives
each plugin its own module name, so the two meet in a registry kept in sys.modules
under a fixed name. The add-on adds its entry when Hermes loads it and removes it on
unload; generalize runs whatever is registered at call time. Load order does not
matter, and both Hermes isolation modes run the plugins of a profile in one
interpreter.

An entry is a dict:

    api     API (1)
    name    short detector name for results, e.g. "laya"
    detect  callable(text, skip) -> list of {"start", "end", "label", "score"}.
            skip is a sorted list of (start, end) spans the regex layer already
            claimed; the detector should not score candidates that overlap them.

An add-on signals a known failure (its dependency is missing, a download failed)
by raising an exception with a ``hint`` attribute; the hint is passed on to the
agent. Other exceptions are reported by type only, because their messages may
quote file content.
"""

from __future__ import annotations

import os
import sys
import time
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from .detect import Candidate, Category, Finding, _to_finding
from .inventory import SourceFile

REGISTRY = "metaskill_extras"
API = 1

# The extras together get this long per generalize call. A tool call that runs for
# many minutes looks hung, so detection stops at the next file boundary and says so.
TIME_LIMIT_ENV = "METASKILL_EXTRAS_MAX_SECONDS"
DEFAULT_TIME_LIMIT = 600.0

Span = tuple[int, int]


def registry() -> dict[str, dict]:
    """The shared registry. Add-ons hold a copy of this function, not an import of it."""
    fresh = types.ModuleType(REGISTRY)
    fresh.detectors = {}
    return sys.modules.setdefault(REGISTRY, fresh).detectors


def registered() -> list[dict]:
    """Valid entries, by name. An entry for another API version is ignored."""
    entries = [
        entry for entry in list(registry().values())
        if isinstance(entry, dict) and entry.get("api") == API
        and isinstance(entry.get("name"), str) and callable(entry.get("detect"))
    ]
    return sorted(entries, key=lambda entry: entry["name"])


def time_limit() -> float:
    try:
        return max(0.0, float(os.environ.get(TIME_LIMIT_ENV, DEFAULT_TIME_LIMIT)))
    except ValueError:
        return DEFAULT_TIME_LIMIT


@dataclass
class ExtraRun:
    name: str
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    warning: str = ""


def run_extra(entry: dict, files: list[SourceFile], claimed: dict[str, list[Span]],
              deadline: float, clock: Callable[[], float] = time.monotonic) -> ExtraRun:
    """Run one extra detector over every file, until it fails or the deadline passes."""
    run = ExtraRun(entry["name"])
    for source in files:
        if clock() >= deadline:
            run.warning = (
                f"{run.name} stopped at the time limit after {run.files_scanned} of "
                f"{len(files)} files; raise {TIME_LIMIT_ENV} to scan more"
            )
            break
        skip = claimed.get(source.path, [])
        try:
            hits = entry["detect"](source.text, skip)
        except Exception as exc:  # an add-on failing must not fail generalize
            hint = getattr(exc, "hint", "")
            run.warning = f"{run.name} detector failed ({type(exc).__name__})" + (
                f": {hint}" if isinstance(hint, str) and hint else "")
            break
        run.files_scanned += 1
        run.findings += _to_findings(run.name, source, hits, skip)
    return run


def _to_findings(name: str, source: SourceFile, hits: Iterable, skip: list[Span]) -> list[Finding]:
    """Findings for valid hits. Hits out of range, overlapping a claimed span or an
    earlier hit are dropped, so an add-on cannot double-count or report regex values."""
    taken = sorted(skip)
    findings = []
    for hit in hits if isinstance(hits, (list, tuple)) else ():
        try:
            start, end = int(hit["start"]), int(hit["end"])
            score = float(hit.get("score", 0.0))
            label = str(hit.get("label", "")) or "personal data"
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if not 0 <= start < end <= len(source.text):
            continue
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        rule = f"{name}:{label.lower().replace(' ', '_')}"
        candidate = Candidate(start, end, Category.PERSONAL_DATA, rule, round(score, 4))
        findings.append(replace(_to_finding(source, candidate), detector=name))
    return findings
