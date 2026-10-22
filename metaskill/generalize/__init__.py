"""Generalize a skill into a reusable template.

Part 1 of the metaskill plugin. Detects personal data in a skill with the
Laya Experts PII model and reports counts. Redaction and placeholder output
are not implemented yet, so no files are rewritten.

All Laya-specific code lives in ``laya_pii/``; this module only routes to it.

Entry point (imported by tools._load_entry_point)::

    from metaskill.generalize import generalize_skill
    generalize_skill(skill_path, output_path)
"""

from __future__ import annotations

from . import laya_pii
from .laya_pii.skill_scan import scan_skill


def generalize_skill(skill_path: str, output_path: str,
                     use_laya: bool = False, laya_model_path: str = "") -> dict:
    """Detect personal data across *skill_path* and report counts.

    Laya is opt-in via ``use_laya``. The regular (non-Laya) detection path is
    developed separately and plugs in at the marked spot below.
    ``output_path`` is reserved for the template output, which is not written yet.
    Only counts and file totals are returned; detected values never leave this
    function.
    """
    if not use_laya:
        # TODO(regular-detection): replace with the default detector once it is merged.
        return {"status": "error", "error": "generalize without use_laya is not implemented yet"}
    try:
        laya_pii.load_detector(laya_model_path)  # fail fast before walking files
    except laya_pii.LayaUnavailable as exc:
        return {
            "status": "error",
            "error": str(exc),
            "laya_available": False,
            # Read by the model: install is large and needs the user's say-so.
            "next_step": (
                "Ask the user whether they want to install the optional Laya PII model "
                "(about 1 GB). If yes, tell them to run `hermes plugins install "
                "metaskill-laya` and then retry. Do not install it without their consent."
            ),
        }

    entities_detected, files_scanned, files_over_limit = scan_skill(skill_path, laya_model_path)
    warnings = []
    if files_over_limit:
        warnings.append(f"{files_over_limit} file(s) over the size limit were skipped")
    return {
        "status": "ok",
        "message": "generalize detection pass complete",
        "detectors": ["laya"],  # optional layers are reported in the result (design.md)
        "artifacts": [],
        "counts": {
            "entities_detected": entities_detected,
            "files_scanned": files_scanned,
        },
        "unresolved": [],
        "warnings": warnings,
    }
