"""Generalize a skill into a reusable template.

Part 1 of the metaskill plugin. Detects personal data in a skill with the
Laya Experts PII model and reports counts. Redaction and placeholder output
are not implemented yet, so no files are rewritten.

Entry point (imported by tools._load_entry_point)::

    from metaskill.generalize import generalize_skill
    generalize_skill(skill_path, output_path)
"""

from __future__ import annotations

import os

from . import laya_pii  # relative: Hermes loads the plugin under its own package name

# --- allowlist --- #
_ALLOWED_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                ".py", ".sh", ".js", ".ts"}
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv"}
_LIMITED_FILES = 500
_LIMITED_BYTES = 25 * 1024 * 1024  # 25 MiB total
_FILE_BYTES = 1024 * 1024  # 1 MiB per file


def generalize_skill(skill_path: str, output_path: str,
                     use_laya: bool = False, laya_model_path: str = "") -> dict:
    """Detect personal data across *skill_path* and report counts.

    Laya is opt-in via ``use_laya``. The regular (non-Laya) detection path is
    developed on another branch and plugs in at the marked spot below.
    ``output_path`` is reserved for the template output, which is not written yet.
    Only counts and file totals are returned; detected values never leave this
    function.
    """
    if not use_laya:
        # TODO(regular-detection): replaced by the default detector from the other branch.
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

    entities_detected, files_scanned, files_over_limit = _scan_skill(skill_path, laya_model_path)
    warnings = []
    if files_over_limit:
        warnings.append(f"{files_over_limit} file(s) over the size limit were skipped")
    return {
        "status": "ok",
        "message": "generalize detection pass complete",
        "artifacts": [],
        "counts": {
            "entities_detected": entities_detected,
            "files_scanned": files_scanned,
        },
        "unresolved": [],
        "warnings": warnings,
    }


def _scan_skill(skill_path: str, laya_model_path: str) -> tuple[int, int, int]:
    """Return ``(entities_detected, files_scanned, files_over_limit)`` for *skill_path*."""
    root = os.path.abspath(skill_path)
    if os.path.isfile(root):
        return len(_scan_file(root, laya_model_path)), 1, 0

    entities_detected = 0
    files_scanned = 0
    files_over_limit = 0
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # never descend into junk folders (and never follow symlinks)
        dirnames[:] = [
            d for d in dirnames
            if os.path.abspath(os.path.join(dirpath, d)) != root
            and d not in _SKIP_DIRS
            and not os.path.islink(os.path.join(dirpath, d))
        ]
        for name in filenames:
            if files_scanned >= _LIMITED_FILES:
                return entities_detected, files_scanned, files_over_limit
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            if any(part.startswith(".") for part in rel.split(os.sep)):
                continue
            if os.path.splitext(name)[1].lower() not in _ALLOWED_EXT:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > _FILE_BYTES:
                files_over_limit += 1
                continue
            total_bytes += size
            if total_bytes > _LIMITED_BYTES:
                files_over_limit += 1
                break
            files_scanned += 1
            entities_detected += len(_scan_file(full, laya_model_path))

    return entities_detected, files_scanned, files_over_limit


def _scan_file(full: str, laya_model_path: str) -> list[dict]:
    """Detect personal data in one text file. Unreadable files are skipped."""
    try:
        with open(full, "r", encoding="utf-8-sig", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return []
    return laya_pii.scan(text, laya_model_path)
