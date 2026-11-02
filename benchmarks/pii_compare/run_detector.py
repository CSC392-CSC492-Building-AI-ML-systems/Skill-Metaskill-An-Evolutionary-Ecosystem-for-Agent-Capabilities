"""Run one detector over the labeled cases and write its findings as JSON.

    python run_detector.py regex <repo-root-with-PR7> out_regex.json
    python run_detector.py laya  <repo-root-with-PR15> out_laya.json [threshold]

The two detectors live in different branches that both define
``metaskill.generalize``, so each runs in its own process against its own checkout.
Findings are offsets into the case text; compare.py does the scoring.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES  # noqa: E402


def run_regex(repo: Path) -> dict:
    sys.path.insert(0, str(repo))
    from metaskill.generalize import scan_skill

    out = {}
    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            # newline="" keeps the CRLF case byte-for-byte
            with open(Path(tmp) / case["file"], "w", encoding="utf-8", newline="") as fh:
                fh.write(case["text"])
            start = time.time()
            result = scan_skill(tmp)
            elapsed = time.time() - start
        out[case["id"]] = {
            "seconds": elapsed,
            "findings": [
                {"start": f.start, "end": f.end, "label": f.rule, "score": f.confidence,
                 "action": f.action.value}
                for f in result.findings
            ],
        }
    return out


def run_laya(repo: Path, threshold: float) -> dict:
    sys.path.insert(0, str(repo))
    from metaskill.generalize import laya_pii

    laya_pii.load_detector()  # model load time stays out of the per-case timings
    out = {}
    for case in CASES:
        start = time.time()
        entities = laya_pii.scan(case["text"], threshold=threshold)
        out[case["id"]] = {
            "seconds": time.time() - start,
            "findings": [
                {"start": e["start"], "end": e["end"], "label": e["label"], "score": e["score"],
                 "action": "detect"}
                for e in entities
            ],
        }
        print(f"  {case['id']}: {len(entities)} found in {out[case['id']]['seconds']:.1f}s",
              flush=True)
    return out


def main() -> None:
    kind, repo, dest = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if kind == "regex":
        data = run_regex(repo)
    elif kind == "laya":
        data = run_laya(repo, float(sys.argv[4]) if len(sys.argv) > 4 else 0.5)
    else:
        sys.exit("kind must be 'regex' or 'laya'")
    dest.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
