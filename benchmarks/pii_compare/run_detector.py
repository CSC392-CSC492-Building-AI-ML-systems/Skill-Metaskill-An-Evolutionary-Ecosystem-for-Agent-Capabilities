"""Run a detector over the labeled cases and write its findings as JSON.

    python run_detector.py regex out_regex.json
    python run_detector.py laya  out_laya.json      # the add-on alone, on every candidate
    python run_detector.py both  out_both.json      # generalize with the add-on installed

``both`` is what a user gets: the regex layer, then Laya skipping the spans regex
already claimed. ``laya`` and ``both`` need laya installed
(pip install -r plugins/metaskill-laya/requirements.txt). Findings are offsets into
the case text; compare.py does the scoring.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
from cases import CASES  # noqa: E402


def load_addon():
    """Import plugins/metaskill-laya the way Hermes does, and register it with core."""
    sys.modules.setdefault("hermes_plugins", types.ModuleType("hermes_plugins")).__path__ = []
    folder = REPO / "plugins" / "metaskill-laya"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.metaskill_laya", folder / "__init__.py", submodule_search_locations=[str(folder)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    addon.register(object())
    start = time.time()
    addon.detector.load()  # model load time stays out of the per-case timings
    print(f"model loaded in {time.time() - start:.1f}s", flush=True)
    return addon


def write_case(tmp: str, case: dict) -> Path:
    path = Path(tmp) / case["file"]
    with open(path, "w", encoding="utf-8", newline="") as fh:  # keeps the CRLF case as is
        fh.write(case["text"])
    return path


def as_dict(finding) -> dict:
    return {"start": finding.start, "end": finding.end, "label": finding.rule,
            "score": finding.confidence, "action": finding.action.value, "detector": finding.detector}


def run_regex() -> dict:
    from metaskill.generalize import scan_skill

    out = {}
    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp, case)
            start = time.time()
            result = scan_skill(tmp)
            out[case["id"]] = {"seconds": time.time() - start,
                               "findings": [as_dict(f) for f in result.findings]}
    return out


def run_laya() -> dict:
    addon = load_addon()
    out = {}
    for case in CASES:
        start = time.time()
        hits = addon.detector.detect(case["text"])
        out[case["id"]] = {
            "seconds": time.time() - start,
            "findings": [{"start": h["start"], "end": h["end"], "label": h["label"], "score": h["score"],
                          "action": "review", "detector": "laya"} for h in hits],
        }
        print(f"  {case['id']}: {len(hits)} found in {out[case['id']]['seconds']:.1f}s", flush=True)
    return out


def run_both() -> dict:
    from metaskill.generalize import extras, load_inventory
    from metaskill.generalize.detect import detect_file_with_claims

    load_addon()
    [entry] = extras.registered()
    out = {}
    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            write_case(tmp, case)
            start = time.time()
            inventory = load_inventory(tmp)
            findings, claimed = [], {}
            for source in inventory.files:
                found, claimed[source.path] = detect_file_with_claims(source)
                findings += found
            findings += extras.run_extra(entry, inventory.files, claimed, float("inf")).findings
            out[case["id"]] = {"seconds": time.time() - start,
                               "findings": [as_dict(f) for f in findings]}
        print(f"  {case['id']}: {len(findings)} found in {out[case['id']]['seconds']:.1f}s", flush=True)
    return out


def main() -> None:
    kind, dest = sys.argv[1], Path(sys.argv[2])
    runners = {"regex": run_regex, "laya": run_laya, "both": run_both}
    if kind not in runners:
        sys.exit("kind must be one of: " + ", ".join(runners))
    dest.write_text(json.dumps(runners[kind](), indent=1), encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
