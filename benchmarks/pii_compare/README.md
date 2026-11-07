# PII detector comparison: regex layer vs the Laya add-on

Scores the detectors on the same small labeled corpus (`cases.py`): 10 skill-like
files, 34 fake personal values that should be flagged, 12 decoys that should not.

```
pip install -r plugins/metaskill-laya/requirements.txt   # for laya and both

python run_detector.py regex out_regex.json
python run_detector.py laya  out_laya.json    # the add-on alone, scoring every candidate
python run_detector.py both  out_both.json    # what generalize does with the add-on installed
python compare.py regex=out_regex.json laya=out_laya.json both=out_both.json --misses
```

`both` runs the regex layer, then Laya over the same files, skipping the spans regex
already claimed. `compare.py` can also merge outputs (`union=a.json+b.json`).

Set `METASKILL_LAYA_DEVICE=gpu` to run Laya on a GPU.

Scoring: a labeled value is found when any finding overlaps its span. A finding is a
false positive when it overlaps a decoy or no labeled value. Findings with action
`keep` (environment-variable references) are not counted as flags.

Limits: the corpus is small and was written by people who know both detectors, so treat
the numbers as a smoke test, not an accuracy rate. Grow `cases.py` with real skills
before quoting figures.
