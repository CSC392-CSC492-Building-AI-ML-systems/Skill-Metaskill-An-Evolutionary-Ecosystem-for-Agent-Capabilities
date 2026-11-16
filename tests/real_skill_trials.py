"""MET-17: run generalize then personalize on the four annotated real Hermes skills.

For each skill in tests/fixtures/ground-truth this goes through the same tool handler
Hermes calls (tools.metaskill), so routing, defaults and result shapes are exercised too:

1. generalize the skill into a temporary directory;
2. write a config that gives every placeholder the value it replaced;
3. personalize the template with that config;
4. compare the personalized skill with the original, byte for byte.

A byte-identical round trip means generalization changed no formatting and no behaviour:
the only edits were placeholders, and they map back exactly. It also scores the findings
against the reviewed ground truth (false replacements, unexpected reviews, misses) and
estimates the token cost of the tool, at about 4 characters per token.

When a local Hermes install is found, the original, generalized and personalized SKILL.md
are also parsed with Hermes's own skill loader (agent.skill_utils.parse_frontmatter), so
"the skill still loads" is checked by Hermes rather than by this script. Set
METASKILL_HERMES_AGENT (the hermes-agent checkout) and METASKILL_HERMES_PYTHON (its
interpreter) to point at another install; without one, that check is reported as skipped.

Run `python tests/real_skill_trials.py` from the repository root. `--json` prints the
raw results. Values are never printed: only files, lines, categories and counts.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]

import tools
from ground_truth import case_paths, load, score, skill_dir
from metaskill.generalize.detect import scan_skill

CHARS_PER_TOKEN = 4  # rough estimate for English and code; no tokenizer is required


_HERMES_PARSE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agent.skill_utils import parse_frontmatter
out = []
for path in sys.argv[2:]:
    frontmatter, body = parse_frontmatter(open(path, encoding="utf-8").read())
    out.append({"frontmatter": frontmatter, "body": body})
print(json.dumps(out, default=str))
"""


def _hermes() -> tuple[str, str] | None:
    """The hermes-agent checkout and its Python, if Hermes is installed here."""
    agent = os.environ.get("METASKILL_HERMES_AGENT") or os.path.expanduser("~/.hermes/hermes-agent")
    python = os.environ.get("METASKILL_HERMES_PYTHON") or next(
        iter(sorted(glob.glob(os.path.expanduser("~/.hermes/tools/python-*/bin/python3")))), "")
    if os.path.isfile(os.path.join(agent, "agent", "skill_utils.py")) and os.path.isfile(python):
        return agent, python
    return None


def hermes_parse(paths: list[Path]) -> list[dict] | None:
    """Parse each SKILL.md with Hermes's loader; None when Hermes is not installed."""
    found = _hermes()
    if found is None:
        return None
    agent, python = found
    proc = subprocess.run([python, "-I", "-c", _HERMES_PARSE, agent, *map(str, paths)],
                          capture_output=True, text=True, timeout=120, cwd=agent)
    if proc.returncode != 0:
        raise RuntimeError(f"Hermes skill loader failed: {proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout)


def tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


@dataclass
class Trial:
    skill: str
    generalize_status: str = ""
    personalize_status: str = ""
    detectors: list[str] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)
    actions: dict[str, int] = field(default_factory=dict)
    placeholders: int = 0
    replacements: int = 0
    unresolved_reviews: int = 0
    expected: int = 0
    detected: int = 0
    missed: list[str] = field(default_factory=list)
    false_replacements: list[str] = field(default_factory=list)
    unexpected_reviews: list[str] = field(default_factory=list)
    round_trip_identical: bool = False
    changed_files: list[str] = field(default_factory=list)
    frontmatter_intact: bool = False
    # None: Hermes not installed. Otherwise whether Hermes reads the same name and
    # description from the generalized and personalized SKILL.md as from the original.
    hermes_loads: bool | None = None
    warnings: list[str] = field(default_factory=list)
    tokens_skill: int = 0  # the original SKILL.md, as Hermes loads it
    tokens_generalized_skill: int = 0
    tokens_generalize_result: int = 0  # the tool result that enters the agent's context
    tokens_personalize_result: int = 0
    tokens_findings_report: int = 0  # findings.json, if the agent reads it


def _call(args: dict) -> dict:
    return json.loads(tools.metaskill(args))


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end != -1 else ""


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


def run_trial(case: dict, work: Path) -> Trial:
    source = skill_dir(case)
    trial = Trial(case["skill"])
    generalized = work / "generalized"
    personalized = work / "personalized"

    result = _call({"operation": "generalize", "skill_path": str(source),
                    "output_path": str(generalized)})
    trial.generalize_status = result["status"]
    trial.tokens_generalize_result = tokens(json.dumps(result))
    if result["status"] == "error":
        trial.warnings.append(f"generalize: {result.get('error')}")
        return trial
    trial.detectors = result.get("detectors", [])
    counts = result["counts"]
    trial.categories = counts.get("by_category", {})
    trial.actions = counts.get("by_action", {})
    trial.placeholders = counts["placeholders"]
    trial.replacements = counts["replacements"]
    trial.unresolved_reviews = counts["unresolved_reviews"]
    trial.warnings += [w if isinstance(w, str) else json.dumps(w, sort_keys=True)
                       for w in result.get("warnings", [])]

    report_text = (generalized / "findings.json").read_text(encoding="utf-8")
    trial.tokens_findings_report = tokens(report_text)
    report = json.loads(report_text)

    # Ground truth: the same findings the pipeline used (regex, no add-on installed here).
    scan = scan_skill(source)
    measured = score(case, scan.findings)
    trial.expected, trial.detected = measured.expected, measured.detected
    trial.missed = [f"{f}:{line} ({c})" for f, line, c in measured.missed]
    trial.false_replacements = [f"{f}:{line} ({c})" for f, line, c in measured.false_replacements]
    trial.unexpected_reviews = [f"{f}:{line} ({c})" for f, line, c in measured.unexpected_reviews]

    # A config that restores every replaced value, so the round trip should be exact.
    by_id = {f.id: f for f in scan.findings}
    texts = {s.path: s.text for s in scan.files}
    config: dict[str, str] = {}
    for row in report["findings"]:
        if "placeholder" in row:
            key = row["placeholder"][len("{{config."):-2]
            finding = by_id[row["id"]]
            config[key] = texts[finding.file][finding.start:finding.end]
    config_path = work / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = _call({"operation": "personalize", "skill_path": str(generalized),
                    "config_path": str(config_path), "output_path": str(personalized)})
    trial.personalize_status = result["status"]
    trial.tokens_personalize_result = tokens(json.dumps(result))
    if result["status"] == "error":
        trial.warnings.append(f"personalize: {result.get('error')}")
        return trial
    trial.warnings += [f"personalize: {w}" for w in result.get("warnings", [])]

    original = {path: data for path, data in _files(source).items() if path in texts}
    restored = _files(personalized / "personalized-skill")
    trial.changed_files = sorted(
        path for path in original.keys() | restored.keys()
        if original.get(path) != restored.get(path))
    trial.round_trip_identical = not trial.changed_files

    skill_md = texts.get("SKILL.md", "")
    template_md = (generalized / "generalized-skill" / "SKILL.md").read_text(encoding="utf-8")
    trial.tokens_skill = tokens(skill_md)
    trial.tokens_generalized_skill = tokens(template_md)
    trial.frontmatter_intact = bool(_frontmatter(skill_md)) and (
        _frontmatter(skill_md) == _frontmatter(template_md))

    parsed = hermes_parse([source / "SKILL.md", generalized / "generalized-skill" / "SKILL.md",
                           personalized / "personalized-skill" / "SKILL.md"])
    if parsed is not None:
        original_fm, template_fm, restored_fm = (p["frontmatter"] for p in parsed)
        trial.hermes_loads = (
            bool(original_fm.get("name")) and bool(original_fm.get("description"))
            and original_fm == template_fm == restored_fm
            and parsed[0]["body"] == parsed[2]["body"])
    return trial


def run_all() -> list[Trial]:
    trials = []
    for path in case_paths():
        with tempfile.TemporaryDirectory() as tmp:
            trials.append(run_trial(load(path), Path(tmp)))
    return trials


def main() -> None:
    trials = run_all()
    if "--json" in sys.argv:
        print(json.dumps([asdict(t) for t in trials], indent=2))
        return
    for t in trials:
        print(f"{t.skill}: generalize {t.generalize_status}, personalize {t.personalize_status}, "
              f"round trip {'identical' if t.round_trip_identical else 'CHANGED'}, "
              f"frontmatter {'intact' if t.frontmatter_intact else 'CHANGED'}, Hermes loads "
              f"{'skipped' if t.hermes_loads is None else 'same' if t.hermes_loads else 'CHANGED'}")
        print(f"  categories {t.categories} actions {t.actions}")
        print(f"  placeholders {t.placeholders}, replacements {t.replacements}, "
              f"left for review {t.unresolved_reviews}")
        print(f"  ground truth: detected {t.detected}/{t.expected}, "
              f"false replacements {len(t.false_replacements)}, "
              f"unexpected reviews {len(t.unexpected_reviews)}")
        for label, items in (("missed", t.missed), ("false replacement", t.false_replacements),
                             ("unexpected review", t.unexpected_reviews),
                             ("changed file", t.changed_files)):
            for item in items:
                print(f"    {label}: {item}")
        print(f"  tokens (~{CHARS_PER_TOKEN} chars each): SKILL.md {t.tokens_skill} -> "
              f"{t.tokens_generalized_skill}, generalize result {t.tokens_generalize_result}, "
              f"personalize result {t.tokens_personalize_result}, "
              f"findings.json {t.tokens_findings_report}")
        for w in t.warnings:
            print(f"    warning: {w}")
    totals = Counter()
    for t in trials:
        totals.update(fp=len(t.false_replacements), ur=len(t.unexpected_reviews),
                      exp=t.expected, det=t.detected)
    print(f"total: detected {totals['det']}/{totals['exp']}, false replacements {totals['fp']}, "
          f"unexpected reviews {totals['ur']}, round trips identical "
          f"{sum(t.round_trip_identical for t in trials)}/{len(trials)}")


if __name__ == "__main__":
    main()
