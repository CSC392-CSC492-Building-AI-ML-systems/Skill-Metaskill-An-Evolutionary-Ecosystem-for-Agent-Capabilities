"""End-to-end skill generalization pipeline."""

from __future__ import annotations

import importlib
import json
import os
import re
import secrets
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import (
    PLACEHOLDER_PATTERN,
    ConfigField,
    assign_config_fields,
    build_config_template,
)
from .detect import Action, Candidate, Category, Finding, ScanResult, _to_finding, scan_skill
from .inventory import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, SourceFile
from .transform import replace_spans

_DEFAULT_LAYA_NEXT_STEP = (
    "Install the optional metaskill-laya plugin, restart Hermes, then retry with "
    "use_laya=True."
)
_LABEL_WORDS = re.compile(r"[^a-zA-Z0-9]+")


def _read_decisions(decisions_path: str | Path | None, findings: list[Finding]) -> dict[str, str]:
    if decisions_path is None:
        return {}
    try:
        data = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("decisions file must be readable UTF-8 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("decisions file must map finding IDs to 'approve' or 'skip'")
    review_ids = {finding.id for finding in findings if finding.action is Action.REVIEW}
    for finding_id, decision in data.items():
        if (finding_id not in review_ids or not isinstance(decision, str)
                or decision not in {"approve", "skip"}):
            raise ValueError("decisions file contains an unknown finding or invalid decision")
    return data


def _safe_report_finding(
    finding: Finding,
    *,
    action: str,
    status: str,
    placeholder: str | None = None,
) -> dict[str, Any]:
    """Report location and classification only; never serialize candidate values."""
    row: dict[str, Any] = {
        "id": finding.id,
        "file": finding.file,
        "line": finding.line,
        "column": finding.column,
        "category": finding.category.value,
        "rule": finding.rule,
        "detector": finding.detector,
        "confidence": finding.confidence,
        "original_action": finding.action.value,
        "action": action,
        "status": status,
        "sensitive": finding.sensitive,
    }
    if placeholder:
        row["placeholder"] = f"{{{{config.{placeholder}}}}}"
    return row


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_inventory(result: ScanResult, destination: Path, transformed: dict[str, str]) -> None:
    """Copy only inventoried files, retaining their permission bits."""
    for source in result.files:
        relative = Path(source.path)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(transformed[source.path], encoding="utf-8", newline="")
        source_path = result.root.joinpath(*relative.parts)
        source_stat = source_path.lstat()
        if stat.S_ISLNK(source_stat.st_mode):
            raise ValueError("a scanned source file changed into a symlink")
        os.chmod(target, stat.S_IMODE(source_stat.st_mode))


def _iter_laya_hits(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        raw = raw.get("findings", raw.get("entities", [raw]))
    elif not isinstance(raw, (list, tuple)):
        raw = getattr(raw, "findings", getattr(raw, "entities", []))
    try:
        return list(raw)
    except TypeError:
        return []


def _hit_value(hit: Any, name: str, default: Any = None) -> Any:
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)


def _laya_findings(
    files: list[SourceFile],
    existing: list[Finding],
    model_path: str,
) -> tuple[list[Finding], int, str | None, str | None]:
    """Run the optional laya_pii scanner; unavailable-model errors remain value-free."""
    try:
        module = importlib.import_module("laya_pii")
    except ImportError:
        return [], 0, _DEFAULT_LAYA_NEXT_STEP, "Laya detector is not installed"

    scan = getattr(module, "scan", None)
    if not callable(scan):
        return [], 0, _DEFAULT_LAYA_NEXT_STEP, "Laya package has no scan function"
    unavailable_type = getattr(module, "LayaUnavailable", None)
    unavailable_type = unavailable_type if isinstance(unavailable_type, type) else None

    claimed: dict[str, list[tuple[int, int]]] = {}
    for finding in existing:
        claimed.setdefault(finding.file, []).append((finding.start, finding.end))

    findings: list[Finding] = []
    scanned = 0
    for source in files:
        try:
            hits = scan(source.text, model_path=model_path) if model_path else scan(source.text)
        except Exception as exc:
            unavailable = (
                (unavailable_type is not None and isinstance(exc, unavailable_type))
                or type(exc).__name__ == "LayaUnavailable"
            )
            if unavailable:
                next_step = getattr(exc, "next_step", getattr(exc, "hint", None))
                return findings, scanned, (
                    next_step if isinstance(next_step, str) and next_step else _DEFAULT_LAYA_NEXT_STEP
                ), None
            return findings, scanned, None, f"Laya detector failed ({type(exc).__name__})"
        scanned += 1
        occupied = claimed.setdefault(source.path, [])
        for hit in _iter_laya_hits(hits):
            try:
                start = int(_hit_value(hit, "start"))
                end = int(_hit_value(hit, "end"))
                score = float(_hit_value(hit, "score", _hit_value(hit, "confidence", 0.0)))
                label = str(_hit_value(hit, "label", "personal_data"))
            except (TypeError, ValueError):
                continue
            if not (0 <= start < end <= len(source.text)) or not 0.0 <= score <= 1.0:
                continue
            if any(start < claimed_end and claimed_start < end for claimed_start, claimed_end in occupied):
                continue
            label_key = _LABEL_WORDS.sub("_", label.strip().lower()).strip("_") or "personal_data"
            candidate = Candidate(
                start=start,
                end=end,
                category=Category.PERSONAL_DATA,
                rule=f"laya:{label_key}",
                confidence=round(score, 4),
                name=label_key,
            )
            finding = replace(_to_finding(source, candidate), action=Action.REVIEW, detector="laya")
            findings.append(finding)
            occupied.append((start, end))
    return findings, scanned, None, None


def _output_scan_limits(transformed: dict[str, str], limits: dict[str, int]) -> dict[str, int]:
    """Raise output scan byte bounds to fit the transformed text, including placeholders."""
    byte_sizes = [len(text.encode("utf-8")) for text in transformed.values()]
    return {
        "max_files": max(limits.get("max_files", MAX_FILES), len(transformed)),
        "max_file_bytes": max(limits.get("max_file_bytes", MAX_FILE_BYTES), max(byte_sizes, default=0)),
        "max_total_bytes": max(
            limits.get("max_total_bytes", MAX_TOTAL_BYTES), sum(byte_sizes)
        ),
    }


def _validate_output(
    skill_dir: Path,
    schema: dict[str, Any],
    example: dict[str, str],
    limits: dict[str, int],
    *,
    expected_files: set[str],
    use_laya: bool = False,
    laya_model_path: str = "",
) -> tuple[ScanResult, str | None, str | None]:
    """Check config consistency and rescan every generated file for residuals."""
    output = scan_skill(skill_dir, **limits)
    scanned_paths = {source.path for source in output.files}
    if scanned_paths != expected_files:
        raise ValueError("output validation did not scan every generated file")

    schema_keys = set(schema.get("properties", {}))
    example_keys = set(example)
    placeholder_keys = {
        key for source in output.files for key in PLACEHOLDER_PATTERN.findall(source.text)
    }
    if placeholder_keys - schema_keys:
        raise ValueError("output contains a placeholder without a config schema field")
    if schema_keys != example_keys:
        raise ValueError("config schema and example fields do not match")
    if any(value != "" for value in example.values()):
        raise ValueError("config example values must be blank")
    if set(schema.get("required", [])) != schema_keys:
        raise ValueError("config schema required fields do not match its properties")

    next_step = warning = None
    if use_laya:
        laya, _scanned, next_step, warning = _laya_findings(
            output.files, output.findings, laya_model_path
        )
        output.findings.extend(laya)
    return output, next_step, warning


def _offset_after_replacements(
    finding: Finding,
    replacements: list[tuple[Finding, str]],
) -> int:
    shift = sum(
        len(replacement) - (item.end - item.start)
        for item, replacement in replacements
        if item.end <= finding.start
    )
    return finding.start + shift


def _retained_key(finding: Finding, replacements: list[tuple[Finding, str]]) -> tuple:
    start = _offset_after_replacements(finding, replacements)
    return (finding.file, start, start + (finding.end - finding.start), finding.category, finding.rule)


def _create_output_parents(parent: Path) -> list[Path]:
    missing: list[Path] = []
    current = parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o777)
                created.append(directory)
            except FileExistsError:
                if not directory.is_dir():
                    raise
        return created
    except BaseException:
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _make_stage(parent: Path, name: str) -> Path:
    """Create a unique staging directory with normal umask-derived permissions."""
    for _ in range(100):
        stage = parent / f".{name}-{secrets.token_hex(6)}"
        try:
            stage.mkdir(mode=0o777)
            return stage
        except FileExistsError:
            continue
    raise FileExistsError("could not create a unique output staging directory")


def generalize_skill(
    skill_path: str | Path,
    *,
    output_path: str | Path,
    decisions_path: str | Path | None = None,
    use_laya: bool = False,
    laya_model_path: str = "",
    **limits: int,
) -> dict[str, Any]:
    """Create a generalized skill bundle without modifying the source tree."""
    source_path = Path(skill_path).expanduser()
    if source_path.is_symlink():
        raise ValueError("skill_path must not be a symlink")
    resolved_source = source_path.resolve(strict=True)
    standalone_file = resolved_source.is_file()
    result = scan_skill(resolved_source, **limits)

    next_step = None
    laya_warning = None
    detectors = ["regex"]
    if use_laya:
        laya, laya_files_scanned, next_step, laya_warning = _laya_findings(
            result.files, result.findings, laya_model_path
        )
        result.findings.extend(laya)
        if laya_files_scanned:
            detectors.append("laya")
    decisions = _read_decisions(decisions_path, result.findings)

    source_by_path = {item.path: item for item in result.files}
    selected: list[tuple[Finding, str]] = []
    for finding in result.findings:
        decision = decisions.get(finding.id)
        if finding.action is Action.REPLACE or (
            finding.action is Action.REVIEW and decision == "approve"
        ):
            selected.append((finding, source_by_path[finding.file].text[finding.start:finding.end]))

    original_placeholder_keys = {
        key for source in result.files for key in PLACEHOLDER_PATTERN.findall(source.text)
    }
    generated_fields, placeholders = assign_config_fields(
        selected, reserved_names=original_placeholder_keys
    )
    fields = {
        key: ConfigField(key, Category.CONFIGURATION, False)
        for key in sorted(original_placeholder_keys)
    }
    fields.update(generated_fields)
    schema, example = build_config_template(fields)

    replacements_by_file: dict[str, list[tuple[Finding, str]]] = {}
    for finding, _raw in selected:
        replacements_by_file.setdefault(finding.file, []).append(
            (finding, f"{{{{config.{placeholders[finding.id]}}}}}")
        )
    replacement_texts = {
        path: [(finding, replacement) for finding, replacement in entries]
        for path, entries in replacements_by_file.items()
    }
    transformed = {
        source.path: replace_spans(source.text, replacements_by_file.get(source.path, []))
        for source in result.files
    }

    requested_output = Path(output_path).expanduser()
    if requested_output.is_symlink():
        raise ValueError("output_path must not be a symlink")
    output_dir = requested_output.resolve(strict=False)
    source_root = result.root.resolve(strict=True)
    if not standalone_file and (output_dir == source_root or source_root in output_dir.parents):
        raise ValueError("output_path must be outside the source skill root")
    if standalone_file and output_dir == resolved_source:
        raise ValueError("output_path must not overwrite the standalone source file")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(output_dir)

    created_parents = _create_output_parents(output_dir.parent)
    stage: Path | None = None
    try:
        stage = _make_stage(output_dir.parent, output_dir.name)
        skill_dir = stage / "generalized-skill"
        skill_dir.mkdir(mode=0o777)
        _copy_inventory(result, skill_dir, transformed)

        output_limits = _output_scan_limits(transformed, limits)
        residual, output_next_step, output_laya_warning = _validate_output(
            skill_dir,
            schema,
            example,
            output_limits,
            expected_files=set(transformed),
            use_laya=use_laya,
            laya_model_path=laya_model_path,
        )
        next_step = next_step or output_next_step
        laya_warning = laya_warning or output_laya_warning
        if use_laya and "laya" not in detectors and residual.files:
            # The output pass is useful too; it means the detector did run on generated files.
            if output_next_step is None and output_laya_warning is None:
                detectors.append("laya")

        unresolved_rows: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for finding in result.findings:
            decision = decisions.get(finding.id)
            if finding.id in placeholders:
                action, status = "replace", "replaced"
            elif finding.action is Action.KEEP:
                action, status = "keep", "kept"
            elif decision == "skip":
                action, status = "keep", "skipped"
                unresolved_rows.append(_safe_report_finding(
                    finding, action=action, status=status
                ))
            else:
                action, status = "review", "review"
                unresolved_rows.append(_safe_report_finding(finding, action=action, status=status))
            row = _safe_report_finding(
                finding, action=action, status=status, placeholder=placeholders.get(finding.id)
            )
            if decision:
                row["decision"] = decision
            rows.append(row)
            if unresolved_rows and unresolved_rows[-1].get("id") == finding.id and decision:
                unresolved_rows[-1]["decision"] = decision

        expected_retained = {
            _retained_key(finding, replacement_texts.get(finding.file, []))
            for finding in result.findings
            if finding.action is not Action.KEEP and finding.id not in placeholders
        }
        residual_rows = []
        unexpected_residuals = []
        for finding in residual.findings:
            expected = _retained_key(finding, []) in expected_retained
            status = "kept" if finding.action is Action.KEEP else (
                "expected_review" if expected else "unexpected_residual"
            )
            residual_rows.append(_safe_report_finding(
                finding,
                action="keep" if finding.action is Action.KEEP else "review",
                status=status,
            ))
            if finding.action is not Action.KEEP and not expected:
                unexpected_residuals.append(finding)

        approved_values_remaining = []
        for finding, _raw_value in selected:
            replacement = f"{{{{config.{placeholders[finding.id]}}}}}"
            output_text = transformed[finding.file]
            expected_start = _offset_after_replacements(
                finding, replacement_texts.get(finding.file, [])
            )
            if output_text[expected_start:expected_start + len(replacement)] != replacement:
                approved_values_remaining.append({
                    "id": finding.id,
                    "file": finding.file,
                    "line": finding.line,
                    "category": finding.category.value,
                    "rule": finding.rule,
                })

        findings_report = {
            "version": 1,
            "detectors": detectors,
            "scanned_files": [item.path for item in result.files],
            "skipped_files": [item.to_dict() for item in result.skipped],
            "findings": rows,
            "unresolved_review_findings": unresolved_rows,
            "residual_findings": residual_rows,
            "unexpected_residual_count": len(unexpected_residuals),
            "approved_values_remaining": approved_values_remaining,
            "validation": {
                "all_placeholders_defined": True,
                "schema_and_example_match": True,
                "example_values_blank": True,
                "approved_values_replaced": not approved_values_remaining,
                "residual_detection_ran": True,
                "residual_count": len(residual.findings),
                "unexpected_residual_count": len(unexpected_residuals),
            },
        }
        _write_json(stage / "config.schema.json", schema)
        _write_json(stage / "config.example.json", example)
        _write_json(stage / "findings.json", findings_report)
        os.replace(stage, output_dir)
        stage = None
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        for directory in reversed(created_parents):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise

    warnings = [
        {"file": skipped.path, "reason": skipped.reason.value}
        for skipped in result.skipped
    ]
    if unexpected_residuals:
        warnings.append({"reason": "residual_candidates", "count": len(unexpected_residuals)})
    if approved_values_remaining:
        warnings.append({
            "reason": "approved_values_remain_unexpectedly",
            "count": len(approved_values_remaining),
        })
    if unresolved_rows:
        warnings.append({"reason": "unresolved_review_findings", "count": len(unresolved_rows)})
    if laya_warning:
        warnings.append({"reason": "laya_unavailable", "detail": laya_warning})

    response = {
        "status": "partial" if (
            approved_values_remaining or unresolved_rows or unexpected_residuals
            or (use_laya and (next_step or laya_warning))
        ) else "ok",
        "detectors": detectors,
        "artifacts": [
            str(output_dir / "generalized-skill"),
            str(output_dir / "config.schema.json"),
            str(output_dir / "config.example.json"),
            str(output_dir / "findings.json"),
        ],
        "counts": {
            "files_scanned": len(result.files),
            "findings": len(result.findings),
            "placeholders": len(fields),
            "replacements": len(selected),
            "residual_findings": len(residual.findings),
            "unexpected_residual_findings": len(unexpected_residuals),
            "approved_values_remaining": len(approved_values_remaining),
            "unresolved_reviews": len(unresolved_rows),
        },
        "unresolved": unresolved_rows,
        "warnings": warnings,
    }
    if next_step:
        response["next_step"] = next_step
    return response
