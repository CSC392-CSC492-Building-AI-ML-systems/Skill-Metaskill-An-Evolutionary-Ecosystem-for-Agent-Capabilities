"""Bounded discovery of the files in a skill that detection reads (design.md, section 3.1).

Files are read, never executed or written. Symlinks are never followed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import json
import re
import tomllib
from pathlib import PurePosixPath

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

@dataclass
class SkillProject:
    """Parsed skill information. Source files are read as data and never executed."""

    metadata: dict[str, object] = field(default_factory=dict)
    prompt: str = ""
    tools: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    referenced_files: list[str] = field(default_factory=list)
    environment_variables: list[str] = field(default_factory=list)

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
    project: SkillProject = field(default_factory=SkillProject)


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
    inventory.project = _parse_project(inventory.files)
    return inventory

_ENV_REFERENCE = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*['"](?P<os>[A-Za-z_]\w*)['"]"""
    r"""|process\.env\.(?P<node>[A-Za-z_]\w*)"""
    r"""|\$\{(?P<braced>[A-Za-z_]\w*)\}"""
)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)#?]+)(?:#[^)]*)?\)")
_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))",
    re.M,
)
_JS_IMPORT = re.compile(
    r"""^\s*import\s+(?:[^\n]*?\s+from\s+)?['"]([^./][^'"]*)['"]""",
    re.M,
)


def _scalar(value: str) -> object:
    """Parse common frontmatter scalar and inline-list values without running YAML."""
    value = value.strip()

    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')

    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in inner.split(",") if part.strip()]

    if value.lower() in {"true", "false"}:
        return value.lower() == "true"

    if value.lower() in {"null", "~"}:
        return None

    return value


def _frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Read simple YAML-style SKILL.md frontmatter and return the remaining prompt."""
    lines = text.splitlines(keepends=True)

    if not lines or lines[0].strip() != "---":
        return {}, text

    closing = next(
        (i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}),
        None,
    )
    if closing is None:
        return {}, text

    metadata: dict[str, object] = {}
    active_list: str | None = None
    active_block: tuple[str, int, str] | None = None

    for line in lines[1:closing]:
        indent = len(line) - len(line.lstrip())

        if active_block and indent >= active_block[1] and line.strip():
            key, _, style = active_block
            current = str(metadata.get(key, ""))
            separator = " " if style.startswith(">") else "\n"
            metadata[key] = current + (separator if current else "") + line.strip()
            continue

        active_block = None

        item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if item and active_list:
            current = metadata.setdefault(active_list, [])
            if isinstance(current, list):
                current.append(_scalar(item.group(1)))
            continue

        pair = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", line)
        if not pair:
            continue

        key, value = pair.groups()
        value = value.strip()

        if value in {"|", ">", "|-", ">-", "|+", ">+"}:
            metadata[key] = ""
            active_block = (key, indent + 1, value)
            active_list = None
        elif not value:
            metadata.setdefault(key, [])
            active_list = key
        else:
            metadata[key] = _scalar(value)
            active_list = None

    return metadata, "".join(lines[closing + 1:])


def _safe_reference(value: str) -> str | None:
    value = value.strip().strip("<>")

    if not value or value.startswith(("/", "\\")) or "://" in value:
        return None

    path = PurePosixPath(value)
    if ".." in path.parts:
        return None

    return path.as_posix()


def _parse_project(files: list[SourceFile]) -> SkillProject:
    """Extract project information from inventoried text without executing files."""
    contents = {source.path: source.text for source in files}
    metadata, prompt = _frontmatter(contents.get("SKILL.md", ""))

    tools: list[str] = []
    for key, value in metadata.items():
        if key != "tools" and not key.endswith(".tools"):
            continue

        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if entry in (None, ""):
                continue

            match = re.search(r"(?:^|\s)name\s*:\s*([^,\s]+)", str(entry))
            tools.append(match.group(1).strip("'\"") if match else str(entry))

    for match in re.finditer(r"(?im)^\s*tools?\s*:\s*([^\n]+)$", prompt):
        tools.extend(
            part.strip(" `,*")
            for part in match.group(1).split(",")
            if part.strip(" `,*")
        )

    dependencies: set[str] = set()

    for path, text in contents.items():
        filename = PurePosixPath(path).name

        if filename == "requirements.txt":
            for line in text.splitlines():
                entry = line.split("#", 1)[0].strip()
                if entry and not entry.startswith(("-", ".", "/")):
                    dependencies.add(entry)

        elif filename == "package.json":
            try:
                manifest = json.loads(text)
            except json.JSONDecodeError:
                manifest = {}

            if isinstance(manifest, dict):
                for section in (
                    "dependencies",
                    "devDependencies",
                    "optionalDependencies",
                ):
                    entries = manifest.get(section, {})
                    if isinstance(entries, dict):
                        dependencies.update(
                            f"{name}@{version}" for name, version in entries.items()
                        )

        elif filename == "pyproject.toml":
            try:
                manifest = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                manifest = {}

            project = manifest.get("project", {})
            if isinstance(project, dict):
                dependencies.update(
                    str(item) for item in project.get("dependencies", [])
                )

                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    for group in optional.values():
                        if isinstance(group, list):
                            dependencies.update(str(item) for item in group)

        if path.lower().endswith((".py", ".js", ".ts")):
            for match in _PY_IMPORT.finditer(text):
                dependencies.add(match.group(1) or match.group(2))
            dependencies.update(_JS_IMPORT.findall(text))

    references: set[str] = set()
    for text in contents.values():
        for raw in _MARKDOWN_LINK.findall(text):
            reference = _safe_reference(raw)
            if reference:
                references.add(reference)

    environment_variables: set[str] = set()
    for text in contents.values():
        for match in _ENV_REFERENCE.finditer(text):
            name = next(
                (value for value in match.groupdict().values() if value),
                None,
            )
            if name:
                environment_variables.add(name)

    return SkillProject(
        metadata=metadata,
        prompt=prompt,
        tools=sorted(set(tools)),
        dependencies=sorted(dependencies, key=str.casefold),
        referenced_files=sorted(references),
        environment_variables=sorted(environment_variables),
    )

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
    return sorted(files, key=lambda path: path.as_posix()), skipped
