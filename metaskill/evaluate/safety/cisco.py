"""Cisco skill-scanner backend for the Safety dimension.

The only module that imports skill_scanner (docs/safety/cisco.md). It runs the
scanner on one skill and translates the result into our BackendResult. Behaviour
was verified against version 2.2.1.

At the deep tier, Cisco's LLM analyzer reaches the model through Hermes's
`ctx.llm` (hermes_bridge.py), never with a key of its own.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
import stat
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from ..models import Dimension, Finding, SafetyTier, Severity, Skill
from .checks import MAX_FRONTMATTER_VALUES, frontmatter_too_large
from .findings import BackendResult, BackendStatus
from .hermes_bridge import PLACEHOLDER_KEY, LlmUsage, PluginLlm, routed

PACKAGE = "cisco-ai-skill-scanner"
# Pinned in plugins/metaskill-cisco/plugin.yaml too; change both together.
TESTED_VERSION = "2.2.1"
ADDON = "metaskill-cisco"
# The scanner is the optional metaskill-cisco add-on: installing it makes Hermes install the
# scanner into its own environment, after the user consents. pip would reach another Python:
# Hermes' own environment has no pip.
INSTALL_COMMAND = ("hermes plugins install CSC392-CSC492-Building-AI-ML-systems/"
                   f"Skill-Metaskill-An-Evolutionary-Ecosystem-for-Agent-Capabilities#plugins/{ADDON}")

# Name the scanner gives its LLM analyzer in analyzers_used and analyzers_failed.
LLM_ANALYZER = "llm_analyzer"

# Findings the LLM analyzer emits about its own run, not about the skill. The
# scanner calls these diagnostic rules. Passed through, they would lower the score
# of any large skill, or of any skill scanned while the API is down.
SCANNER_NOTICES = {"LLM_ANALYSIS_FAILED", "LLM_CONTEXT_BUDGET_EXCEEDED"}
_BUDGET_NOTICE = "LLM_CONTEXT_BUDGET_EXCEEDED"

# These are not about harm to the user, so they are kept as evidence under
# Configuration Cleanliness and not scored here (docs/safety/cisco.md).
ROUTED_CATEGORIES = {
    "hardcoded_secrets": Dimension.CONFIG_CLEANLINESS,
    "policy_violation": Dimension.CONFIG_CLEANLINESS,
}


class LLMSetupError(Exception):
    """The scanner's LLM analyzer could not be configured."""


class CiscoBackend:
    name = "cisco"

    def __init__(self, llm: PluginLlm | None = None, task: str | None = None,
                 provider: str = "", model: str = "") -> None:
        # Hermes's ctx.llm, used only at the deep tier. Without it, Cisco runs static.
        # task/provider/model are the Hermes model settings to call through (judge.py's
        # JUDGE_TASK and EvaluationConfig.judge_provider/judge_model), so Cisco and the
        # review run on the same model even when the user pins it through provider/model.
        self.llm = llm
        self.task = task
        self.provider = provider
        self.model = model
        # What the LLM analyzer's calls used, for the report header; empty at the static tier.
        self.llm_usage = LlmUsage()

    def available(self) -> bool:
        return importlib.util.find_spec("skill_scanner") is not None

    def version(self) -> str:
        """Installed scanner version, for ReportMeta.scanner_version; "" if missing."""
        try:
            return importlib.metadata.version(PACKAGE)
        except importlib.metadata.PackageNotFoundError:
            return ""

    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult:
        if not self.available():
            return BackendResult(
                self.name, BackendStatus.NOT_INSTALLED, tier,
                detail=(f"Cisco skill-scanner not installed (the optional {ADDON} add-on); "
                        f"run `{INSTALL_COMMAND}` in a "
                        "terminal, then restart Hermes"))
        try:
            # The scanner parses the frontmatter itself, and its time doubles with each
            # level of nested aliases (2.2.1: 6s at 2^24 values, minutes past 2^28). Our
            # own checks report the frontmatter as a HIGH finding instead.
            if frontmatter_too_large(skill.frontmatter):
                return BackendResult(
                    self.name, BackendStatus.ERROR, tier,
                    detail=(f"not run: the frontmatter expands to more than "
                            f"{MAX_FRONTMATTER_VALUES} values through YAML aliases, which "
                            "would stall the scanner"))
            with tempfile.TemporaryDirectory(prefix="metaskill-cisco-") as tmp:
                # The scanner reads the folder itself and, at the deep tier, sends what it
                # reads to the model provider. A skill from git can hold a link such as
                # notes.md -> ../../.hermes/.env, so it scans a copy without links, as the
                # judge only reads files that are the skill's (judge.skill_sources).
                # The folder keeps its name: the scanner may compare it with the frontmatter.
                source = Path(skill.path)
                copy, left_out = _without_links(source, Path(tmp) / (source.resolve().name or "skill"))
                if "SKILL.md" in left_out:
                    return BackendResult(
                        self.name, BackendStatus.ERROR, tier,
                        detail="not run: SKILL.md is a link or hard link, so it may be a "
                               "file outside the skill")
                result = self._scan_copy(copy, tier)
            if left_out:
                note = f"{len(left_out)} link(s) left out, not scanned: {', '.join(left_out[:5])}"
                result.detail = "; ".join(p for p in (result.detail, note) if p)
            return result
        except Exception as e:  # never raise into the caller
            return BackendResult(
                self.name, BackendStatus.ERROR, tier,
                detail=f"{type(e).__name__}: {e}")

    def _scan_copy(self, path: Path, tier: SafetyTier) -> BackendResult:
        if tier is SafetyTier.DEEP:
            if self.llm is None:
                result = convert(self._run_scanner(path, None), SafetyTier.STATIC)
                return _fell_back(result, "no Hermes LLM access (ctx.llm), so Cisco ran static only")
            with self._route() as model:
                try:
                    analyzers = self._build_deep_analyzers(model)
                except LLMSetupError as e:
                    result = convert(self._run_scanner(path, None), SafetyTier.STATIC)
                    return _fell_back(result, str(e))
                return convert(self._run_scanner(path, analyzers), tier)
        return convert(self._run_scanner(path, None), tier)

    # --- The only methods that touch skill_scanner or LiteLLM. Tests replace them. ---

    def _route(self) -> AbstractContextManager[str]:
        """Make self.llm reachable by Cisco; yields the model name to give it."""
        return routed(self.llm, self.task, self.provider, self.model, usage=self.llm_usage)

    def _build_deep_analyzers(self, model: str) -> list[Any]:
        from skill_scanner.core.analyzer_factory import (
            AnalyzerConfigurationError, build_analyzers)
        from skill_scanner.core.scan_policy import ScanPolicy

        try:
            # The model name routes every request to ctx.llm. The key is a placeholder
            # Cisco requires; no real key is ever given to Cisco or read from the environment.
            return build_analyzers(
                ScanPolicy.default(), use_llm=True, llm_model=model, llm_api_key=PLACEHOLDER_KEY)
        except AnalyzerConfigurationError as e:
            raise LLMSetupError(str(e)) from e

    def _run_scanner(self, path: Path, analyzers: list[Any] | None) -> dict[str, Any]:
        """Scan one skill directory; None means the scanner's default static analyzers."""
        from skill_scanner import SkillScanner

        return SkillScanner(analyzers=analyzers).scan_skill(path).to_dict()


# --- Conversion: pure functions over ScanResult.to_dict() output ---------------


def convert(scan: dict[str, Any], tier: SafetyTier) -> BackendResult:
    """Turn one ScanResult.to_dict() into our BackendResult.

    tier is the tier that was run. A deep scan whose LLM analyzer failed at run
    time comes back as llm-unavailable at the static tier, keeping static findings.
    """
    raw = scan.get("findings", [])
    notices = [f for f in raw if f.get("rule_id") in SCANNER_NOTICES]
    findings = [
        _convert_finding(f) for f in raw
        if f.get("rule_id") not in SCANNER_NOTICES and f.get("severity") != "SAFE"
    ]

    failed = scan.get("analyzers_failed", [])
    llm_errors = [f.get("error", "") for f in failed if f.get("analyzer") == LLM_ANALYZER]
    other_failures = [f for f in failed if f.get("analyzer") != LLM_ANALYZER]

    notes = []
    skipped = [f.get("file_path") or "" for f in notices if f.get("rule_id") == _BUDGET_NOTICE]
    if skipped:
        notes.append(
            f"LLM analysis skipped or cut {len(skipped)} file(s) over its context budget: "
            + ", ".join(skipped))
    for f in other_failures:
        notes.append(f"analyzer {f.get('analyzer')} failed: {f.get('error', '')}")

    result = BackendResult(
        CiscoBackend.name, BackendStatus.OK, tier, findings, "; ".join(notes))
    llm_missing = LLM_ANALYZER not in scan.get("analyzers_used", [])
    if tier is SafetyTier.DEEP and (llm_errors or llm_missing):
        reason = llm_errors[0] if llm_errors else "LLM analyzer did not run"
        return _fell_back(result, f"LLM analysis failed: {reason}")
    return result


def _without_links(source: Path, dest: Path) -> tuple[Path, list[str]]:
    """Copy the skill at source to dest without links: every regular file and folder,
    hidden ones too (the scanner looks at those), but no symlink and no hard link, either
    of which could be a file outside the skill. Returns dest and the paths left out,
    relative to source with `/`, sorted."""
    left_out: list[str] = []
    dest.mkdir(parents=True)
    for folder, dirs, files in os.walk(source, followlinks=False):
        here = Path(folder)
        rel = here.relative_to(source)
        for name in list(dirs):
            if (here / name).is_symlink():
                dirs.remove(name)  # not descended into
                left_out.append((rel / name).as_posix())
            else:
                (dest / rel / name).mkdir()
        for name in files:
            path = here / name
            try:
                info = path.lstat()
            except OSError:
                continue  # gone or unreadable: the scanner could not read it either
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                left_out.append((rel / name).as_posix())
                continue
            shutil.copyfile(path, dest / rel / name, follow_symlinks=False)
    return dest, sorted(left_out)


def _fell_back(result: BackendResult, reason: str) -> BackendResult:
    """Mark a result as static-only because the LLM layer did not run."""
    result.status = BackendStatus.LLM_UNAVAILABLE
    result.tier = SafetyTier.STATIC
    result.detail = "; ".join(p for p in (reason, result.detail) if p)
    return result


def _convert_finding(f: dict[str, Any]) -> Finding:
    category = f["category"]
    title, description = f.get("title") or "", f.get("description") or ""
    return Finding(
        dimension=ROUTED_CATEGORIES.get(category, Dimension.SAFETY),
        category=category,
        severity=Severity(f["severity"]),
        source=CiscoBackend.name,
        rule=f["rule_id"],
        quote=f.get("snippet") or "",
        location=_location(f.get("file_path"), f.get("line_number")),
        message=f"{title}: {description}" if title and description else title or description,
    )


def _location(file_path: str | None, line_number: int | None) -> str:
    if not file_path:
        return ""
    return f"{file_path}:{line_number}" if line_number is not None else file_path
