"""Bounded discovery of the files in a skill that detection reads (design.md, section 3.1).

Files are read, never executed or written. Symlinks are never followed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

EXTENSIONS = frozenset({
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".sh", ".js", ".ts",
})
SPECIAL_NAMES = frozenset({".env", ".npmrc", ".pypirc", "Dockerfile", "Makefile"})
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".tox",
})

MAX_FILES = 500
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024


class SkipReason(StrEnum):
    UNSUPPORTED = "unsupported"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    NOT_UTF8 = "not_utf8"
    SYMLINK = "symlink"
    UNREADABLE = "unreadable"  # permission denied or another OS error


class InventoryError(ValueError):
    """The skill is over the file-count or total-size limit, so it is not scanned at all."""


@dataclass(frozen=True)
class SourceFile:
    """One eligible file. text is the exact decoded bytes, so offsets into it round-trip."""

    path: str  # relative to the skill root, with / separators
    text: str = field(repr=False)  # may hold secrets, so never printed


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: SkipReason

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason.value}


@dataclass
class Inventory:
    root: Path
    files: list[SourceFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)


def is_eligible(path: Path) -> bool:
    name = path.name
    if path.suffix.lower() in EXTENSIONS or name in SPECIAL_NAMES or name.startswith(".env."):
        return True
    if path.suffix:
        return False
    with path.open("rb") as f:
        return f.read(2) == b"#!"


def load_inventory(
    skill_path: Path | str,
    *,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> Inventory:
    """List and read the eligible files of a skill directory, or a standalone SKILL.md."""
    path = Path(skill_path)
    if path.is_symlink():
        raise InventoryError(f"{path}: refusing to scan a symlink")
    if path.is_file():
        root, candidates, skipped = path.parent, [path], []
    elif path.is_dir():
        root = path
        candidates, skipped = _walk(root)
    else:
        raise FileNotFoundError(path)

    eligible: list[tuple[Path, int]] = []
    for file in candidates:
        rel = file.relative_to(root).as_posix()
        try:
            supported = is_eligible(file)
            size = file.stat().st_size if supported else 0
        except OSError:
            skipped.append(SkippedFile(rel, SkipReason.UNREADABLE))
            continue
        if not supported:
            skipped.append(SkippedFile(rel, SkipReason.UNSUPPORTED))
            continue
        if size > max_file_bytes:
            skipped.append(SkippedFile(rel, SkipReason.TOO_LARGE))
            continue
        eligible.append((file, size))

    if len(eligible) > max_files:
        raise InventoryError(
            f"{root}: {len(eligible)} eligible files is over the limit of {max_files}")
    total = sum(size for _, size in eligible)
    if total > max_total_bytes:
        raise InventoryError(
            f"{root}: {total} bytes of eligible files is over the limit of {max_total_bytes}")

    inventory = Inventory(root=root)
    for file, _ in eligible:
        rel = file.relative_to(root).as_posix()
        try:
            data = file.read_bytes()
        except OSError:
            skipped.append(SkippedFile(rel, SkipReason.UNREADABLE))
            continue
        if b"\x00" in data:
            skipped.append(SkippedFile(rel, SkipReason.BINARY))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(SkippedFile(rel, SkipReason.NOT_UTF8))
            continue
        inventory.files.append(SourceFile(rel, text))
    inventory.skipped = sorted(skipped, key=lambda s: s.path)
    return inventory


def _walk(root: Path) -> tuple[list[Path], list[SkippedFile]]:
    files: list[Path] = []
    skipped: list[SkippedFile] = []

    def skip(path: Path, reason: SkipReason) -> None:
        skipped.append(SkippedFile(path.relative_to(root).as_posix(), reason))

    def unreadable_dir(error: OSError) -> None:
        skip(Path(error.filename), SkipReason.UNREADABLE)

    for dirpath, dirnames, filenames in os.walk(root, onerror=unreadable_dir, followlinks=False):
        here = Path(dirpath)
        kept_dirs = []
        for name in sorted(dirnames):
            if name in IGNORED_DIRS:
                continue
            if (here / name).is_symlink():
                skip(here / name, SkipReason.SYMLINK)
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            file = here / name
            try:
                if file.is_symlink():
                    skip(file, SkipReason.SYMLINK)
                elif file.is_file():
                    files.append(file)
            except OSError:
                skip(file, SkipReason.UNREADABLE)
    return sorted(files), skipped
