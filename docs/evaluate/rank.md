# `rank.py` — Overall score and ranking

Code: [`metaskill/evaluate/rank.py`](../../metaskill/evaluate/rank.py). Design: [evaluate.md](../evaluate.md), sections 2 and 5.

`rank.py` turns each skill's six dimension scores into `overall`, orders the skills, notes what a reader should know about each, and recommends one. It never runs anything or calls a model: it works on `SkillResult`s ([models.md](models.md)) that the scoring modules have filled in.

```python
from metaskill.evaluate.rank import make_scores, rank, table

scores = make_scores(dimension_scores, config.weights)   # Scores for a SkillResult
ranking = rank(results, config.weights)                  # list[SkillResult] -> Ranking
print(table(ranking))                                    # the terminal table
```

## 1. Functions

| Function | Description |
|---|---|
| `overall(values, weights)` | Weighted mean of the six dimensions; `None` if any dimension of weight above 0 has no value |
| `weighted_mean(values, weights)` | Weighted mean over the dimensions that have a value, leaving the others out of the sum and the weight total; `None` if none has. This is the mean shown for a skill that is not ranked |
| `make_scores(dimension_scores, weights)` | `Scores` from `{Dimension: DimensionScore}`. A dimension missing from the mapping counts as `None` |
| `rank(results, weights)` | `Ranking` of the given `SkillResult`s (2) |
| `table(ranking)` | The Markdown table of evaluate.md section 2, then the notes and the recommendation |
| `values(scores)` | The six dimension values of a `Scores`, keyed by `Dimension` |

`weights` defaults to `DEFAULT_WEIGHTS` in `models.py`: safety 4, quality 3, robustness 3, clarity 2, efficiency 2, configuration cleanliness 1 (evaluate.md 5.1). They are relative: the mean divides by their sum.

## 2. Ranking

`rank` recomputes `overall` from the dimension scores and `weights`, rather than reading `Scores.overall`, so the order never depends on an `overall` computed elsewhere with other weights.

1. Skills whose `overall` is a number are ranked `1..k`, ordered by `overall`, then `lift`, then safety, quality, robustness, clarity, efficiency and configuration cleanliness (`TIE_BREAK`), then name. Scores closer than `1e-9` count as equal, so floating-point noise does not decide a place.
2. Skills with a dimension that scored `None` follow, with `rank` and `overall` `None`. They are ordered the same way, by their `weighted_mean` instead of `overall`. A skill with no score at all comes last.

The order does not depend on the order of the input, and the same input always gives the same output.

### `Ranking`

| Field | Description |
|---|---|
| `ranked` | `RankedSkill`s with a rank, best first |
| `unranked` | `RankedSkill`s without one, in the order above |
| `entries` | `ranked + unranked` |
| `recommendation` | Which skill to use (4) |

### `RankedSkill`

| Field | Description |
|---|---|
| `result` | The `SkillResult` |
| `rank` | `1..k`, or `None` if not ranked |
| `overall` | `None` exactly when `rank` is |
| `partial_overall` | `weighted_mean` over the scored dimensions; equal to `overall` for a ranked skill, `None` if nothing was scored |
| `missing` | Dimensions of weight above 0 that scored `None` |
| `notes` | Lines for the report (3) |

## 3. Notes

- **Not ranked.** Which dimensions are missing, with their status and detail when the result has them (`clarity (skipped: no API key)`), the mean over the scored dimensions, where that mean would place it among the ranked skills, and its best and worst dimension. The placing is indicative only: the ranked skills' `overall` also counts the dimensions this skill is missing. **[OPEN]** Whether to keep it ([evaluate.md](../evaluate.md), open question 2).
- **Partial safety.** The safety status is `partial`, e.g. a `deep` scan that fell back to `static`. The skill is still ranked, but its safety score is not comparable with full scans.
- **CRITICAL safety findings.** How many, and where. The skill stays in the ranking (evaluate.md 5.2).

## 4. Recommendation

- At least one ranked skill: the top one, `Use [name]: highest overall (0.88) of 3 ranked skill(s).` If it has a CRITICAL safety finding or partial safety, this adds `Check its safety notes before using it.`; otherwise, if its safety score is below 0.5, it says so.
- No ranked skill: the best unranked one on its scored dimensions, with a warning to rerun the missing ones before relying on it.
- No skills: `No skills to rank.`

## 5. Table

```
| Rank | Skill | Overall | Quality | Robustness | Clarity | Config Cleanliness | Efficiency | Safety |
|---|---|---|---|---|---|---|---|---|
| 1 | gcp-file-transfer | 0.88 | 0.90 | 0.85 | 0.80 | 0.90 | 0.85 | 0.95 |
| - | ftp-mirror | (0.82) | 0.85 | 0.80 | - | 0.75 | 0.80 | 0.85 |

Notes:
- ftp-mirror: Not ranked: no score for clarity (skipped: no API key). ...

Recommendation: Use gcp-file-transfer: highest overall (0.88) of 1 ranked skill(s).
```

A skill that is not ranked has `-` as its rank, its `partial_overall` in parentheses, and `-` for each dimension without a score.

## 6. Testing

[`tests/test_rank.py`](../../tests/test_rank.py) builds `SkillResult`s directly. It covers the default weights and their order of priority, the weighted mean, weight validation, ordering and every tie-break, that the order does not depend on input order, unranked skills and their notes, partial safety and CRITICAL findings, the recommendation and the table.
