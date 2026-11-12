"""End-to-end deterministic skill generalization pipeline."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .config import PLACEHOLDER_PATTERN, assign_config_fields, build_config_template
from .detect import Action, Finding, ScanResult, scan_skill
from .transform import replace_spans


def _read_decisions(
    decisions_path: str | Path | None, findings: list[Finding]
) -> dict[str, str]:
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
        if (
            finding_id not in review_ids
            or not isinstance(decision, str)
            or decision not in {"approve", "skip"}
        ):
            raise ValueError("decisions file contains an unknown finding or invalid decision")

    return data


def _safe_report_finding(
    finding: Finding,
    *,
    action: str,
    status: str,
    placeholder: str | None = None,
) -> dict[str, Any]:
    """Report classification and location only; never include candidate values."""
    row = {
        "id": finding.id,
        "file": finding.file,
        "line": finding.line,
        "column": finding.column,
        "category": finding.category.value,
        "rule": finding.rule,
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


def _copy_inventory(
    result: ScanResult, destination: Path, transformed: dict[str, str]
) -> None:
    for source in result.files:
        target = destination.joinpath(*Path(source.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            transformed.get(source.path, source.text),
            encoding="utf-8",
            newline="",
        )


def _validate_output(
    skill_dir: Path,
    schema: dict[str, Any],
    example: dict[str, str],
    limits: dict[str, int],
) -> ScanResult:
    """Validate config consistency and scan generated files for residual findings."""
    output = scan_skill(skill_dir, **limits)
    schema_keys = set(schema.get("properties", {}))
    example_keys = set(example)
    placeholder_keys = {
        key
        for source in output.files
        for key in PLACEHOLDER_PATTERN.findall(source.text)
    }

    if placeholder_keys - schema_keys:
        raise ValueError("output contains a placeholder without a config schema field")
    if schema_keys != example_keys:
        raise ValueError("config schema and example fields do not match")
    if any(value != "" for value in example.values()):
        raise ValueError("config example values must be blank")
    if set(schema.get("required", [])) != schema_keys:
        raise ValueError("config schema required fields do not match its properties")

    return output


def generalize_skill(
    skill_path: str | Path,
    *,
    output_path: str | Path,
    decisions_path: str | Path | None = None,
    **limits: int,
) -> dict[str, Any]:
    """Write a generalized skill and its config and findings artifacts."""
    source_path = Path(skill_path).expanduser()
    if source_path.is_symlink():
        raise ValueError("skill_path must not be a symlink")

    result = scan_skill(source_path, **limits)
    decisions = _read_decisions(decisions_path, result.findings)
    source_by_path = {item.path: item for item in result.files}

    selected: list[tuple[Finding, str]] = []
    for finding in result.findings:
        decision = decisions.get(finding.id)
        if finding.action is Action.REPLACE or (
            finding.action is Action.REVIEW and decision == "approve"
        ):
            raw = source_by_path[finding.file].text[finding.start : finding.end]
            selected.append((finding, raw))

    fields, placeholders = assign_config_fields(selected)
    schema, example = build_config_template(fields)
    unresolved = [
        {
            "id": finding.id,
            "file": finding.file,
            "line": finding.line,
            "category": finding.category.value,
            **({"decision": "skip"} if decisions.get(finding.id) == "skip" else {}),
        }
        for finding in result.findings
        if finding.action is Action.REVIEW and finding.id not in placeholders
    ]

    replacements_by_file: dict[str, list[tuple[Finding, str]]] = {}
    for finding, _raw in selected:
        replacements_by_file.setdefault(finding.file, []).append(
            (finding, f"{{{{config.{placeholders[finding.id]}}}}}")
        )

    transformed = {
        source.path: replace_spans(
            source.text, replacements_by_file.get(source.path, [])
        )
        for source in result.files
    }

    requested_output = Path(output_path).expanduser()
    if requested_output.is_symlink():
        raise ValueError("output_path must not be a symlink")

    output_dir = requested_output.resolve(strict=False)
    source_root = result.root.resolve(strict=True)
    if output_dir == source_root or source_root in output_dir.parents:
        raise ValueError("output_path must be outside the source skill root")
    if output_dir.exists():
        raise FileExistsError(output_dir)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))

    try:
        skill_dir = stage / "generalized-skill"
        skill_dir.mkdir()
        _copy_inventory(result, skill_dir, transformed)
        residual = _validate_output(skill_dir, schema, example, limits)
        _write_json(stage / "config.schema.json", schema)
        _write_json(stage / "config.example.json", example)

        rows = []
        for finding in result.findings:
            decision = decisions.get(finding.id)
            if finding.id in placeholders:
                action, status = "replace", "replaced"
            elif finding.action is Action.KEEP:
                action, status = "keep", "kept"
            elif decision == "skip":
                action, status = "keep", "skipped"
            else:
                action, status = "review", "review"

            row = _safe_report_finding(
                finding,
                action=action,
                status=status,
                placeholder=placeholders.get(finding.id),
            )
            if decision:
                row["decision"] = decision
            rows.append(row)

        residual_rows = [
            _safe_report_finding(
                finding,
                action=finding.action.value,
                status="kept" if finding.action is Action.KEEP else "residual",
            )
            for finding in residual.findings
        ]
        unexpected_residuals = [
            finding for finding in residual.findings if finding.action is not Action.KEEP
        ]

        unresolved_rows = []
        for finding in result.findings:
            if finding.action is not Action.REVIEW or finding.id in placeholders:
                continue
            decision = decisions.get(finding.id)
            unresolved_rows.append(
                _safe_report_finding(
                    finding,
                    action="keep" if decision == "skip" else "review",
                    status="skipped" if decision == "skip" else "review",
                )
            )
            if decision:
                unresolved_rows[-1]["decision"] = decision

        approved_values_remaining = []
        for finding, raw_value in selected:
            output_file = skill_dir.joinpath(*Path(finding.file).parts)
            output_text = output_file.read_text(encoding="utf-8")
            if raw_value and raw_value in output_text:
                approved_values_remaining.append(
                    {
                        "id": finding.id,
                        "file": finding.file,
                        "line": finding.line,
                        "category": finding.category.value,
                        "rule": finding.rule,
                    }
                )

        findings_report = {
            "version": 1,
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
        _write_json(stage / "findings.json", findings_report)
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    warnings = [
        {"file": skipped.path, "reason": skipped.reason.value}
        for skipped in result.skipped
    ]
    if unexpected_residuals:
        warnings.append({"reason": "residual_candidates", "count": len(unexpected_residuals)})
    if approved_values_remaining:
        warnings.append(
            {
                "reason": "approved_values_remain_unexpectedly",
                "count": len(approved_values_remaining),
            }
        )
    if unresolved:
        warnings.append({"reason": "unresolved_review_findings", "count": len(unresolved)})

    return {
        "status": "partial"
        if approved_values_remaining or unresolved or unexpected_residuals
        else "ok",
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
            "unresolved_reviews": len(unresolved),
        },
        "unresolved": unresolved,
        "warnings": warnings,
    }
