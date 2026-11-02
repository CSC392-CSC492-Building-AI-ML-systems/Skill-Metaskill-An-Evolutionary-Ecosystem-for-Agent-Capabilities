# PII detector comparison: regex (PR #7) vs Laya (PR #15)

Scores both detectors on the same small labeled corpus (`cases.py`): 10 skill-like
files, 34 fake personal values that should be flagged, 12 decoys that should not.

The two branches both define `metaskill.generalize`, so each detector runs in its own
process against its own checkout, then `compare.py` scores the JSON outputs.

```
# 1. regex detector: needs a checkout of PR #7 (git worktree add <dir> <pr-7-branch>)
python run_detector.py regex <pr7-checkout> out_regex.json

# 2. Laya detector: needs laya installed (pip install -r optional_requirements.txt)
python run_detector.py laya <this-checkout> out_laya.json

# 3. score; "a=x+y" merges two outputs (what running both layers would flag)
python compare.py regex=out_regex.json laya=out_laya.json both=out_regex.json+out_laya.json --misses
```

Scoring: a labeled value is found when any finding overlaps its span. A finding is a
false positive when it overlaps a decoy or no labeled value. Findings with action
`keep` (env-var names) are not counted as flags.

Limits: the corpus is small and written by the people who know both detectors, so treat
the numbers as a smoke test, not an accuracy rate. Grow `cases.py` with real skills
before quoting figures.
