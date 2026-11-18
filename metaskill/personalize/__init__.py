"""Skill personalization: fill a generalized skill's placeholders from a config (design.md, section 4).

personalize_skill() reads the output of generalize (generalized-skill/ and config.schema.json),
validates a user's config against the schema and writes a separate personalized copy. Nothing
is executed, the inputs are never modified, and no config value appears in the result or report.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..generalize.inventory import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES
from .config import PersonalizeError, load_json, load_schema, validate_config
from .transform import substitute
from .validate import check_output_dir, destination

TEMPLATE_DIR = "generalized-skill"
SCHEMA_FILE = "config.schema.json"
SKILL_DIR = "personalized-skill"
REPORT_FILE = "personalize-report.json"
_MARKER = b"{{config."

__all__ = ["PersonalizeError", "personalize_skill"]


@dataclass(frozen=True)
class _TemplateFile:
    path: str  # relative to the template root, with / separators
    data: bytes = field(repr=False)
    mode: int


def personalize_skill(skill_path: Path | str, config_path: Path | str,
                      output_path: Path | str) -> dict:
    """Write a personalized copy of a generalized skill and return a summary without values.

    A refused run returns status "error" with every problem found and writes nothing.
    """
    try:
        return _personalize(Path(skill_path), Path(config_path), Path(output_path))
    except PersonalizeError as exc:
        return {"status": "error", "error": str(exc), "errors": exc.errors}


def _personalize(skill_path: Path, config_path: Path, output: Path) -> dict:
    bundle, template, schema_path = _locate(skill_path)
    schema = load_schema(schema_path)
    values, warnings = validate_config(schema, load_json(config_path, "config"))
    check_output_dir(output, bundle)
    files, dirs, skipped = _read_template(template)
    warnings += skipped

    resolved: Counter = Counter()
    unresolved: list[dict] = []
    rendered: list[tuple[_TemplateFile, bytes]] = []
    for file in files:
        data, warning = file.data, None
        if b"\x00" in data or len(data) > MAX_FILE_BYTES:
            warning = "binary or too large to substitute"
        else:
            try:
                result = substitute(data.decode("utf-8"), values)
            except UnicodeDecodeError:
                warning = "not UTF-8"
            else:
                data = result.text.encode("utf-8")
                resolved.update(result.resolved)
                unresolved += [{"file": file.path, "line": line, "placeholder": key}
                               for key, line in result.unresolved]
        if warning and _MARKER in data:
            warnings.append(f"{file.path}: copied unchanged, {warning}")
        rendered.append((file, data))

    undeclared = sorted({u["placeholder"] for u in unresolved} - set(schema.fields))
    if undeclared:
        warnings.append("placeholders not declared in the schema were left unchanged: "
                        + ", ".join(undeclared))
    unused = sorted(set(values) - set(resolved))
    if unused:
        warnings.append("config entries not used by any placeholder: " + ", ".join(unused))

    substitutions = sum(resolved.values())
    report = {
        "status": "ok",
        "skill": SKILL_DIR,
        "files": len(files),
        "substitutions": substitutions,
        "placeholders_resolved": dict(sorted(resolved.items())),
        "unresolved": unresolved,
        "unused_config": unused,
        "warnings": warnings,
    }
    _write(output, dirs, rendered, report)
    return {
        "status": "ok",
        "artifacts": [str(output / SKILL_DIR), str(output / REPORT_FILE)],
        "counts": {"files": len(files), "substitutions": substitutions,
                   "placeholders_resolved": len(resolved)},
        "unresolved": [f"{u['file']}:{u['line']} {{{{config.{u['placeholder']}}}}}"
                       for u in unresolved],
        "unused_config": unused,
        "warnings": warnings,
    }


def _locate(path: Path) -> tuple[Path, Path, Path]:
    """Accept the generalize output directory, or the generalized-skill folder inside it."""
    if (path / TEMPLATE_DIR).is_dir():
        bundle = path
    elif path.name == TEMPLATE_DIR and (path.parent / SCHEMA_FILE).exists():
        bundle = path.parent
    else:
        raise PersonalizeError([f"skill path: expected the output of generalize, a directory "
                                f"containing {TEMPLATE_DIR}/ and {SCHEMA_FILE}"])
    template, schema = bundle / TEMPLATE_DIR, bundle / SCHEMA_FILE
    if template.is_symlink():
        raise PersonalizeError([f"skill path: refusing to read {TEMPLATE_DIR} through a symlink"])
    if not schema.is_file():
        raise PersonalizeError([f"skill path: {SCHEMA_FILE} is missing"])
    return bundle, template, schema


def _read_template(root: Path) -> tuple[list[_TemplateFile], list[str], list[str]]:
    """Read every file of the template. Symlinks are skipped and never followed."""
    files: list[_TemplateFile] = []
    dirs: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    total = 0

    def rel(path: Path | str) -> str:
        return Path(path).relative_to(root).as_posix()

    def unreadable(error: OSError) -> None:
        errors.append(f"{rel(error.filename)}: cannot be read")

    for dirpath, dirnames, filenames in os.walk(root, onerror=unreadable, followlinks=False):
        here = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            if (here / name).is_symlink():
                warnings.append(f"{rel(here / name)}: symlink skipped, not followed")
            else:
                kept.append(name)
                dirs.append(rel(here / name))
        dirnames[:] = kept
        for name in sorted(filenames):
            path = here / name
            try:
                if path.is_symlink() or not path.is_file():
                    warnings.append(f"{rel(path)}: skipped, not a regular file")
                    continue
                stat = path.stat()
                if len(files) + 1 > MAX_FILES or total + stat.st_size > MAX_TOTAL_BYTES:
                    raise PersonalizeError([f"{TEMPLATE_DIR}: over the limit of {MAX_FILES} "
                                            f"files or {MAX_TOTAL_BYTES} bytes"])
                data = path.read_bytes()
            except OSError:
                errors.append(f"{rel(path)}: cannot be read")
                continue
            total += len(data)
            files.append(_TemplateFile(rel(path), data, stat.st_mode & 0o777))
    if errors:
        raise PersonalizeError(errors)
    return sorted(files, key=lambda f: f.path), sorted(dirs), warnings


def _write(output: Path, dirs: list[str], rendered: list[tuple[_TemplateFile, bytes]],
           report: dict) -> None:
    """Build the result beside the output and rename it into place, so failures leave nothing."""
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        os.chmod(staging, 0o755)
        skill = staging / SKILL_DIR
        skill.mkdir()
        for directory in dirs:
            destination(skill, directory).mkdir(parents=True, exist_ok=True)
        for file, data in rendered:
            path = destination(skill, file.path)
            path.write_bytes(data)
            os.chmod(path, file.mode)
        (staging / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            output.rmdir()  # check_output_dir accepted it only when empty
        os.rename(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
