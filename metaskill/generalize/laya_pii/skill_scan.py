"""Walk a skill directory and count personal data found by Laya.

Stopgap: this file walker duplicates what the generalize inventory stage does
(design.md, section 3.2 step 1). Once that stage is merged, delete this module
and feed the inventory's files to ``scan`` instead.
"""

from __future__ import annotations

import os

from . import scan

_ALLOWED_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                ".py", ".sh", ".js", ".ts"}
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv"}
_LIMITED_FILES = 500
_LIMITED_BYTES = 25 * 1024 * 1024  # 25 MiB total
_FILE_BYTES = 1024 * 1024  # 1 MiB per file


def scan_skill(skill_path: str, model_path: str = "") -> tuple[int, int, int]:
    """Return ``(entities_detected, files_scanned, files_over_limit)`` for *skill_path*."""
    root = os.path.abspath(skill_path)
    if os.path.isfile(root):
        return len(_scan_file(root, model_path)), 1, 0

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
            entities_detected += len(_scan_file(full, model_path))

    return entities_detected, files_scanned, files_over_limit


def _scan_file(full: str, model_path: str) -> list[dict]:
    """Detect personal data in one text file. Unreadable files are skipped."""
    try:
        with open(full, "r", encoding="utf-8-sig", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return []
    return scan(text, model_path)
