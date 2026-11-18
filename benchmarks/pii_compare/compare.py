"""Score detector outputs against the labeled cases and print a side-by-side report.

    python compare.py regex=out_regex.json laya=out_laya.json [--misses]

A gold value counts as found when any finding overlaps its span. A finding is a
false positive when it overlaps a decoy, or overlaps no gold value at all (every
personal value in the corpus is labeled, so an unmatched finding is noise).
Detectors may also be combined, e.g. ``union=regex+laya``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES  # noqa: E402


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def spans_of(case):
    text = case["text"]
    gold = []
    for group, value in case["gold"]:
        i = text.find(value)
        assert i >= 0 and text.find(value, i + 1) < 0, f"{case['id']}: {value!r} not unique"
        gold.append((group, value, i, i + len(value)))
    decoys = []
    for value in case["decoys"]:
        i = text.find(value)
        assert i >= 0, f"{case['id']}: decoy {value!r} missing"
        decoys.append((value, i, i + len(value)))
    return gold, decoys


def score(findings_by_case: dict) -> dict:
    stats = {"gold": {"structured": [0, 0], "freeform": [0, 0]}, "fp": 0, "decoy_hits": 0,
             "findings": 0, "seconds": 0.0, "missed": [], "false_pos": []}
    for case in CASES:
        entry = findings_by_case.get(case["id"], {"findings": [], "seconds": 0.0})
        # KEEP means the detector noted a config interface (e.g. an env var name) and
        # leaves it in place, so it is not a flag.
        findings = [f for f in entry["findings"] if f.get("action") != "keep"]
        stats["seconds"] += entry["seconds"]
        stats["findings"] += len(findings)
        gold, decoys = spans_of(case)
        for group, value, s, e in gold:
            found = any(overlaps(s, e, f["start"], f["end"]) for f in findings)
            stats["gold"][group][1] += 1
            stats["gold"][group][0] += found
            if not found:
                stats["missed"].append((case["id"], group, value))
        for f in findings:
            hits_decoy = [d for d, s, e in decoys if overlaps(s, e, f["start"], f["end"])]
            hits_gold = any(overlaps(s, e, f["start"], f["end"]) for _, _, s, e in gold)
            if hits_decoy or not hits_gold:
                stats["fp"] += 1
                stats["decoy_hits"] += bool(hits_decoy)
                text = next(c["text"] for c in CASES if c["id"] == case["id"])
                stats["false_pos"].append((case["id"], text[f["start"]:f["end"]], f["label"]))
    return stats


def combine(parts: list[dict]) -> dict:
    merged = {}
    for case in CASES:
        merged[case["id"]] = {
            "seconds": sum(p.get(case["id"], {}).get("seconds", 0.0) for p in parts),
            "findings": [f for p in parts for f in p.get(case["id"], {}).get("findings", [])],
        }
    return merged


def pct(found, total):
    return f"{100 * found / total:5.1f}%" if total else "  n/a "


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = {}
    for arg in args:
        name, _, src = arg.partition("=")
        runs[name] = combine([json.loads(Path(p).read_text()) for p in src.split("+")]) \
            if "+" in src else json.loads(Path(src).read_text())
    results = {name: score(data) for name, data in runs.items()}

    total_gold = sum(len(c["gold"]) for c in CASES)
    print(f"Corpus: {len(CASES)} files, {total_gold} labeled personal values, "
          f"{sum(len(c['decoys']) for c in CASES)} decoys\n")
    names = list(results)
    print(f"{'':<26}" + "".join(f"{n:>14}" for n in names))

    def row(label, fn):
        print(f"{label:<26}" + "".join(f"{fn(results[n]):>14}" for n in names))

    def recall(r, group=None):
        groups = [group] if group else ["structured", "freeform"]
        return sum(r["gold"][g][0] for g in groups), sum(r["gold"][g][1] for g in groups)

    row("recall (all)", lambda r: pct(*recall(r)))
    row("  structured", lambda r: pct(*recall(r, "structured")))
    row("  free-form", lambda r: pct(*recall(r, "freeform")))
    row("false positives", lambda r: str(r["fp"]))
    row("  on decoys", lambda r: str(r["decoy_hits"]))
    row("precision", lambda r: pct(r["findings"] - r["fp"], r["findings"]))
    row("total findings", lambda r: str(r["findings"]))
    row("detector time (s)", lambda r: f"{r['seconds']:.1f}")

    if "--misses" in sys.argv:
        for n in names:
            print(f"\n[{n}] missed:")
            for cid, group, value in results[n]["missed"]:
                print(f"  {cid:<18} {group:<10} {value}")
            print(f"[{n}] false positives:")
            for cid, text, label in results[n]["false_pos"]:
                print(f"  {cid:<18} {text!r} ({label})")


if __name__ == "__main__":
    main()
