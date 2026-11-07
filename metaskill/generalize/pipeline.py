"""generalize_skill: the entry point the metaskill tool calls (tools._ENTRY_POINTS).

Detection only, for now: it inventories the skill, runs the regex layer and any extra
detectors from add-on plugins (extras.py), and reports counts. Placeholders and the
template output are the generalization pipeline's job (MET-12) and are not written
yet, so nothing is created under output_path and the skill is never modified.

Until then the status is "partial", so the tool reports success: false: a caller that
checks only that flag must not tell the user the skill was generalized.

The result goes into the agent's context, so it carries counts only, never a
detected value.
"""

from __future__ import annotations

import time
from collections import Counter

from . import extras
from .detect import Finding, detect_file_with_claims
from .inventory import InventoryError, load_inventory

ADDON_INSTALL = (
    "hermes plugins install CSC392-CSC492-Building-AI-ML-systems/"
    "Skill-Metaskill-An-Evolutionary-Ecosystem-for-Agent-Capabilities#plugins/metaskill-laya"
)
# Read by the model when no add-on is installed. The add-on is large and slow, so it
# is offered, never pushed, and only the user decides to install it.
ADDON_NOTE = (
    "Optional add-on metaskill-laya (not installed) also finds names, street addresses, "
    "birth dates, and ID and card numbers, using a local model (about 1 GB, slower). "
    "Mention it only if the user wants deeper detection, and ask before they install it: "
    f"{ADDON_INSTALL}"
)


def generalize_skill(skill_path: str, output_path: str) -> dict:
    """Detect personal and configuration values across *skill_path* and report counts."""
    try:
        inventory = load_inventory(skill_path)
    except InventoryError as exc:  # the message names the limit, never file content
        return {"status": "error", "error": str(exc)}

    findings: list[Finding] = []
    claimed: dict[str, list[tuple[int, int]]] = {}
    for source in inventory.files:
        found, claimed[source.path] = detect_file_with_claims(source)
        findings += found

    detectors, warnings = ["regex"], []
    entries = extras.registered()
    deadline = time.monotonic() + extras.time_limit()
    for entry in entries:
        run = extras.run_extra(entry, inventory.files, claimed, deadline)
        findings += run.findings
        if run.files_scanned:
            detectors.append(run.name)
        if run.warning:
            warnings.append(run.warning)
    if inventory.skipped:
        reasons = Counter(s.reason.value for s in inventory.skipped)
        warnings.append("skipped files: " + ", ".join(f"{n} {r}" for r, n in sorted(reasons.items())))

    result = {
        "status": "partial",
        "message": ("detection only: the skill was NOT generalized. Placeholders and the "
                    "template are not implemented yet, so no files were written. The counts "
                    "are valid; tell the user nothing was generalized."),
        "detectors": detectors,
        "artifacts": [],
        "counts": {
            "files_scanned": len(inventory.files),
            "files_skipped": len(inventory.skipped),
            "findings": len(findings),
            "by_action": dict(sorted(Counter(f.action.value for f in findings).items())),
            "by_category": dict(sorted(Counter(f.category.value for f in findings).items())),
            "by_detector": dict(sorted(Counter(f.detector for f in findings).items())),
        },
        "unresolved": [],
        "warnings": warnings,
    }
    if not entries:
        result["note"] = ADDON_NOTE
    return result
