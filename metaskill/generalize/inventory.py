"""Bounded discovery and parsing of a skill project (design.md, section 3.1).

Files are read, never executed or written. Symlinks are never followed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

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
    UNREADABLE = "unreadable"


class InventoryError(ValueError):
    """Raised when a skill exceeds a file-count or total-size limit."""


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: SkipReason

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason.value}


@dataclass(repr=False)
class SkillProject:
    """Extracted project data; repr intentionally hides source-derived values."""

    metadata: dict[str, object] = field(default_factory=dict, repr=False)
    prompt: str = field(default="", repr=False)
    tools: list[str] = field(default_factory=list, repr=False)
    # Only declared package-manager requirements belong here.
    dependencies: list[str] = field(default_factory=list, repr=False)
    # Import names stay separate from declared/installable dependencies.
    imported_modules: list[str] = field(default_factory=list, repr=False)
    referenced_files: list[str] = field(default_factory=list, repr=False)
    environment_variables: list[str] = field(default_factory=list, repr=False)

    def __repr__(self) -> str:
        return (
            "SkillProject("
            f"metadata_keys={len(self.metadata)}, tools={len(self.tools)}, "
            f"dependencies={len(self.dependencies)}, imported_modules={len(self.imported_modules)}, "
            f"referenced_files={len(self.referenced_files)}, "
            f"environment_variables={len(self.environment_variables)})"
        )


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
    """List and read eligible files in a skill directory or standalone SKILL.md."""
    path = Path(skill_path).expanduser()
    if path.is_symlink():
        raise InventoryError(f"{path}: refusing to scan a symlink")
    path = path.resolve(strict=True)
    if path.is_file():
        root, candidates, skipped = path.parent, [path], []
    elif path.is_dir():
        root = path
        candidates, skipped = _walk(root, max_files=max_files)
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
        elif size > max_file_bytes:
            skipped.append(SkippedFile(rel, SkipReason.TOO_LARGE))
        else:
            eligible.append((file, size))

    if len(eligible) > max_files:
        raise InventoryError(f"{root}: {len(eligible)} eligible files is over the limit of {max_files}")
    total = sum(size for _, size in eligible)
    if total > max_total_bytes:
        raise InventoryError(f"{root}: {total} bytes of eligible files is over the limit of {max_total_bytes}")

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
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(SkippedFile(rel, SkipReason.NOT_UTF8))
            continue
        inventory.files.append(SourceFile(rel, decoded))

    inventory.skipped = sorted(skipped, key=lambda item: item.path)
    inventory.project = _parse_project(inventory.files)
    return inventory


_ENV_REFERENCE = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*['\"](?P<os_call>[A-Za-z_][A-Za-z0-9_]*)['\"]"
    r"|os\.environ\[\s*['\"](?P<os_index>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"
    r"|process\.env(?:\.(?P<node_dot>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\[['\"](?P<node_index>[A-Za-z_][A-Za-z0-9_]*)['\"]\])"
    r"|\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}"
)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)#?]+)(?:#[^)]*)?\)")
_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))",
    re.M,
)
_JS_IMPORT = re.compile(r"^\s*import\s+(?:[^\n]*?\s+from\s+)?['\"]([^./][^'\"]*)['\"]", re.M)
_CREDENTIAL = re.compile(
    r"(?i)(?:https?://[^\s/@:]+:[^\s/@]+@|\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|sk-[A-Za-z0-9_-]{12,})\b|\b(?:token|password|passwd|secret)=\S+)"
)


def _scalar(raw: str) -> object:
    value = raw.strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in re.split(r",\s*(?![^\[]*\])", inner) if part.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "~"} or not value:
        return None
    return value


def _frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the YAML subset used by skill metadata, including nested mappings/lists."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
    if closing is None:
        return {}, text
    raw = [line.rstrip("\r\n") for line in lines[1:closing]]

    def skip_blank(start: int) -> int:
        while start < len(raw) and (not raw[start].strip() or raw[start].lstrip().startswith("#")):
            start += 1
        return start

    def parse_value(value_raw: str, start: int, indent: int) -> tuple[object, int]:
        marker = value_raw.strip()
        if marker in {"|", ">", "|-", ">-", "|+", ">+"}:
            block_lines: list[str] = []
            block_indent: int | None = None
            while start < len(raw):
                candidate = raw[start]
                if candidate.strip():
                    candidate_indent = len(candidate) - len(candidate.lstrip())
                    if candidate_indent <= indent:
                        break
                    block_indent = candidate_indent if block_indent is None else min(block_indent, candidate_indent)
                block_lines.append(candidate)
                start += 1
            base = block_indent if block_indent is not None else indent + 1
            content = [line[base:] if line.strip() else "" for line in block_lines]
            separator = " " if marker.startswith(">") else "\n"
            return separator.join(content).strip("\n"), start
        if marker:
            return _scalar(marker), start
        child_start = skip_blank(start)
        if child_start < len(raw):
            child_indent = len(raw[child_start]) - len(raw[child_start].lstrip())
            if child_indent > indent:
                return parse_block(child_start, child_indent)
        return None, start

    def parse_block(start: int, indent: int) -> tuple[object, int]:
        start = skip_blank(start)
        if start >= len(raw):
            return {}, start
        first = raw[start]
        first_indent = len(first) - len(first.lstrip())
        if first_indent != indent:
            return {}, start

        if first[indent:].startswith("-"):
            result: list[object] = []
            while start < len(raw):
                start = skip_blank(start)
                if start >= len(raw):
                    break
                line = raw[start]
                current_indent = len(line) - len(line.lstrip())
                if current_indent < indent:
                    break
                if current_indent != indent:
                    break
                item_match = re.match(r"-\s*(.*)$", line[indent:])
                if not item_match:
                    break
                item_raw = item_match.group(1)
                start += 1
                item_pair = re.match(r"([A-Za-z0-9_.-]+)\s*:\s*(.*)$", item_raw)
                if item_pair:
                    key, value_raw = item_pair.groups()
                    value, start = parse_value(value_raw, start, indent)
                    item: dict[str, object] = {key: value}
                    continuation = skip_blank(start)
                    if continuation < len(raw):
                        continuation_indent = len(raw[continuation]) - len(raw[continuation].lstrip())
                        if continuation_indent > indent:
                            extra, start = parse_block(continuation, continuation_indent)
                            if isinstance(extra, dict):
                                item.update(extra)
                    result.append(item)
                elif item_raw:
                    item, start = parse_value(item_raw, start, indent)
                    result.append(item)
                else:
                    child_start = skip_blank(start)
                    if child_start < len(raw) and len(raw[child_start]) - len(raw[child_start].lstrip()) > indent:
                        item, start = parse_block(child_start, len(raw[child_start]) - len(raw[child_start].lstrip()))
                    else:
                        item = None
                    result.append(item)
            return result, start

        result: dict[str, object] = {}
        while start < len(raw):
            start = skip_blank(start)
            if start >= len(raw):
                break
            line = raw[start]
            current_indent = len(line) - len(line.lstrip())
            if current_indent < indent:
                break
            if current_indent != indent:
                break
            pair = re.match(r"([A-Za-z0-9_.-]+)\s*:\s*(.*)$", line[indent:])
            if not pair:
                start += 1
                continue
            key, value_raw = pair.groups()
            start += 1
            result[key], start = parse_value(value_raw, start, indent)
        return result, start

    parsed, _ = parse_block(0, 0)
    metadata = parsed if isinstance(parsed, dict) else {}
    body = "".join(lines[closing + 1:])
    return metadata, body


def _safe_reference(value: str) -> str | None:
    value = value.strip().strip("<>")
    if not value or value.startswith(("/", "\\")) or "://" in value or re.match(r"^[A-Za-z]:", value):
        return None
    path = PurePosixPath(value)
    if ".." in path.parts:
        return None
    return path.as_posix()


def _tool_names(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in values:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


def _body_tools(prompt: str) -> list[str]:
    """Read bullets only under an explicit Markdown Tools heading."""
    found: list[str] = []
    in_tools = False
    for line in prompt.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            in_tools = heading.group(1).strip().casefold() in {"tools", "available tools", "tool declarations"}
            continue
        if not in_tools:
            continue
        bullet = re.match(r"^\s{0,6}[-*+]\s+(.+?)\s*$", line)
        if bullet:
            raw = bullet.group(1).strip(" `*_")
            match = re.match(r"(?:name\s*:\s*)?([A-Za-z0-9_.:-]+)", raw, re.I)
            if match:
                found.append(match.group(1))
    return found


def _safe_dependency(value: str) -> bool:
    return not _CREDENTIAL.search(value)


def _declared_dependencies(path: str, text: str) -> set[str]:
    basename = PurePosixPath(path).name
    result: set[str] = set()
    if basename == "requirements.txt":
        for line in text.splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry and not entry.startswith(("-", ".", "/")) and _safe_dependency(entry):
                result.add(entry)
    elif basename == "package.json":
        try:
            manifest = json.loads(text)
        except json.JSONDecodeError:
            manifest = {}
        if isinstance(manifest, dict):
            for section in ("dependencies", "devDependencies", "optionalDependencies"):
                entries = manifest.get(section, {})
                if isinstance(entries, dict):
                    for name, version in entries.items():
                        entry = f"{name}@{version}"
                        if _safe_dependency(entry):
                            result.add(entry)
    elif basename == "pyproject.toml":
        try:
            manifest = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            manifest = {}
        project = manifest.get("project", {})
        if isinstance(project, dict):
            result.update(str(dep) for dep in project.get("dependencies", [])
                          if isinstance(dep, str) and _safe_dependency(dep))
            optional = project.get("optional-dependencies", {})
            if isinstance(optional, dict):
                result.update(str(dep) for group in optional.values() if isinstance(group, list)
                              for dep in group if isinstance(dep, str) and _safe_dependency(dep))
        poetry = manifest.get("tool", {}).get("poetry", {}) if isinstance(manifest.get("tool", {}), dict) else {}
        if isinstance(poetry, dict):
            deps = poetry.get("dependencies", {})
            if isinstance(deps, dict):
                for name, spec in deps.items():
                    if str(name).casefold() == "python":
                        continue
                    if isinstance(spec, dict):
                        spec = spec.get("version", "")
                    entry = f"{name}{spec}" if spec else str(name)
                    if _safe_dependency(entry):
                        result.add(entry)
            groups = poetry.get("group", {})
            if isinstance(groups, dict):
                for group in groups.values():
                    group_deps = group.get("dependencies", {}) if isinstance(group, dict) else {}
                    if isinstance(group_deps, dict):
                        for name, spec in group_deps.items():
                            if isinstance(spec, dict):
                                spec = spec.get("version", "")
                            entry = f"{name}{spec}" if spec else str(name)
                            if _safe_dependency(entry):
                                result.add(entry)
    return result


def _parse_project(files: list[SourceFile]) -> SkillProject:
    by_path = {source.path: source.text for source in files}
    skill_text = by_path.get("SKILL.md", "")
    metadata, prompt = _frontmatter(skill_text)

    tools: list[str] = []
    frontmatter_tools = metadata.get("tools")
    tools.extend(_tool_names(frontmatter_tools))
    hermes = metadata.get("metadata", {})
    if isinstance(hermes, dict):
        hermes_config = hermes.get("hermes", {})
        if isinstance(hermes_config, dict):
            tools.extend(_tool_names(hermes_config.get("tools")))
    tools.extend(_body_tools(prompt))

    dependencies: set[str] = set()
    imported_modules: set[str] = set()
    for path, text in by_path.items():
        dependencies.update(_declared_dependencies(path, text))
        suffix = PurePosixPath(path).suffix.lower()
        if suffix == ".py":
            for match in _PY_IMPORT.finditer(text):
                module = match.group(1) or match.group(2)
                top_level = module.split(".", 1)[0]
                if top_level not in sys.stdlib_module_names:
                    imported_modules.add(top_level)
        elif suffix in {".js", ".ts"}:
            imported_modules.update(_JS_IMPORT.findall(text))

    references: set[str] = set()
    for raw in _MARKDOWN_LINK.findall(skill_text):
        reference = _safe_reference(raw)
        if reference:
            references.add(reference)

    environment_variables: set[str] = set()
    for text in by_path.values():
        for match in _ENV_REFERENCE.finditer(text):
            name = next((value for value in match.groupdict().values() if value), None)
            if name:
                environment_variables.add(name)

    return SkillProject(
        metadata=metadata,
        prompt=prompt,
        tools=sorted(set(tools)),
        dependencies=sorted(dependencies, key=str.casefold),
        imported_modules=sorted(imported_modules, key=str.casefold),
        referenced_files=sorted(references),
        environment_variables=sorted(environment_variables),
    )


def _walk(root: Path, *, max_files: int) -> tuple[list[Path], list[SkippedFile]]:
    files: list[Path] = []
    skipped: list[SkippedFile] = []
    seen_files = 0

    def count_file(path: Path) -> None:
        nonlocal seen_files
        seen_files += 1
        if seen_files > max_files:
            raise InventoryError(f"{root}: project has more than the file limit of {max_files}")

    def skip(path: Path, reason: SkipReason) -> None:
        skipped.append(SkippedFile(path.relative_to(root).as_posix(), reason))

    def unreadable_dir(error: OSError) -> None:
        if error.filename:
            skip(Path(error.filename), SkipReason.UNREADABLE)

    for dirpath, dirnames, filenames in os.walk(root, onerror=unreadable_dir, followlinks=False):
        here = Path(dirpath)
        kept_dirs = []
        for name in sorted(dirnames):
            if name in IGNORED_DIRS:
                continue
            if (here / name).is_symlink():
                count_file(here / name)
                skip(here / name, SkipReason.SYMLINK)
            else:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            file = here / name
            count_file(file)
            try:
                if file.is_symlink():
                    skip(file, SkipReason.SYMLINK)
                elif file.is_file():
                    files.append(file)
            except OSError:
                skip(file, SkipReason.UNREADABLE)
    return sorted(files, key=lambda item: item.as_posix()), skipped
