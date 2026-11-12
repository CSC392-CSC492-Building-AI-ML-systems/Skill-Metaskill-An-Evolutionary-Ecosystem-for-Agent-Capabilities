# `rank.py` — Overall score and ranking

Code: [`metaskill/evaluate/rank.py`](../../metaskill/evaluate/rank.py). Design: [evaluate.md](../evaluate.md), sections 2 and 5.

`rank.py` turns each skill's six dimension scores into `overall`, orders the skills, notes what a reader should know about each, and recommends one. It never runs anything or calls a model: it works on `SkillResult`s ([models.md](models.md)) that the scoring modules have filled in.

```python
from metaskill.evaluate.rank import make_scores, rank, table

scores = make_scores(dimension_scores, config)   # Scores for a SkillResult
ranking = rank(results, config)                  # list[SkillResult] -> Ranking
print(table(ranking))                            # the terminal table
```

## 1. Functions

| Function | Description |
|---|---|
| `make_scores(dimension_scores, config)` | `Scores` from `{Dimension: DimensionScore}`, weighted with `config.weights`. A dimension missing from the mapping counts as `None` |
| `rank(results, config)` | `Ranking` of the given `SkillResult`s, weighted with `config.weights` (2) |
| `table(ranking)` | The Markdown table of evaluate.md section 2, then the notes and the recommendation (5) |
| `markdown(meta, ranking)` | The contents of `ranked-evaluation.md` (5) |
| `overall(values, weights)` | Weighted mean of the six dimensions; `None` if any dimension has no value, whatever its weight |
| `values(scores)` | The six dimension values of a `Scores`, keyed by `Dimension` |

`make_scores` and `rank` both take the `EvaluationConfig`, so the `overall` stored in the report and the order in the ranking always come from the same weights. Both check the weights again (`models.check_weights`), since a config's `weights` can be replaced after it was made. The defaults, `models.DEFAULT_WEIGHTS`, are safety 4, quality 3, robustness 3, clarity 2, efficiency 2, configuration cleanliness 1 (evaluate.md 5.1). They are relative: the mean divides by their sum. `overall` is the low-level piece: it takes the weights directly and expects them checked.

## 2. Ranking

`rank` recomputes `overall` from the dimension scores, rather than reading `Scores.overall`.

1. Skills whose `overall` is a number are ranked, ordered by `overall`, then `lift`, then safety, quality, robustness, clarity, efficiency and configuration cleanliness (`TIE_BREAK`). Skills equal on all of these share a rank, and the next place is skipped (`1, 1, 3`). They are listed by name, which only keeps the output stable and says nothing about them. Scores are compared rounded to 9 decimal places, so floating-point noise does not decide a place. The name is compared as text, since a YAML `name` can be a number. **[OPEN]** Whether ties should be judged more coarsely ([evaluate.md](../evaluate.md), open question 2).
2. Skills with a dimension that scored `None` follow, with `rank` and `overall` `None`. They are ordered by name, since their scores do not compare with each other. Their remaining dimensions are not averaged into a replacement score (evaluate.md 2.1).

The order does not depend on the order of the input, and the same input always gives the same output. `SkillResult` refuses a `lift` outside `[-1, 1]`, NaN included, which would otherwise break this.

### `Ranking`

| Field | Description |
|---|---|
| `ranked` | `RankedSkill`s with a rank, best first |
| `unranked` | `RankedSkill`s without one, in the order above |
| `entries` | `ranked + unranked` |
| `notes` | Notes about the ranking as a whole (3) |
| `recommendation` | Which skill to use (4) |

### `RankedSkill`

| Field | Description |
|---|---|
| `result` | The `SkillResult` |
| `rank` | Place from 1, shared on a full tie; `None` if not ranked |
| `overall` | `None` exactly when `rank` is |
| `missing` | Dimensions that scored `None` |
| `notes` | Lines for the report (3) |
| `name` | The skill's name, cleaned for printing (6) |

## 3. Notes

Per skill:

- **Not ranked.** Which dimensions are missing, with their status and detail when the result has them (`clarity (skipped: no API key)`). Dimensions with the same status and detail are named together (`quality, clarity (skipped: not built yet)`). When no skill is ranked and every one is missing the same, one `Ranking.notes` entry says so instead (`No skill is ranked: no score for ...`).
- **Partial safety.** The safety status is `partial` (e.g. a `deep` scan that fell back to `static`, or a scanner that is not installed) while another skill in the ranking has `ok`. Only then is its safety score not comparable with the others. The skill is still ranked.
- **CRITICAL safety findings.** How many, and where. The skill stays in the ranking (evaluate.md 5.2).

For the ranking as a whole (`Ranking.notes`): when every skill with a safety score is `partial`, one note says so, rather than a flag on each skill, since their scores still compare with each other.

## 4. Recommendation

- At least one ranked skill: the top one, `Use [name]: highest overall (0.88) of 3 ranked skill(s).` If it has a CRITICAL safety finding, or a partial-safety flag (3), this adds `Check its safety notes before using it.`; otherwise, if its safety score is below a MEDIUM finding's score (`LOW_SAFETY`, i.e. HIGH or worse), it says so.
- Several skills tied for first: all of them are named and none is picked, `Tied for highest overall (0.88) of 3 ranked skill(s): a, b. Nothing scored separates them.` The safety check is added if any of them is flagged, and the low-safety and lift warnings still apply.
- No ranked skill: no skill is named, since no complete overall comparison was available: `No complete overall comparison was available, so no skill is recommended.` The notes say what is missing.
- No skills: `No skills to rank.`
- A recommended skill also gets a warning if its `lift` is 0 or less: on the tasks it does no better than having no skill (evaluate.md 4.1). A `lift` of `None`, when no tasks ran, gets no warning.

## 5. Table

```
| Rank | Skill | Overall | Quality | Robustness | Clarity | Config Cleanliness | Efficiency | Safety |
|---|---|---|---|---|---|---|---|---|
| 1 | gcp-file-transfer | 0.88 | 0.90 | 0.85 | 0.80 | 0.90 | 0.85 | 0.95 |
| - | ftp-mirror | - | 0.85 | 0.80 | - | 0.75 | 0.80 | 0.85 |

Notes:
- ftp-mirror: Not ranked: no score for clarity (skipped: no API key).

Recommendation: Use gcp-file-transfer: highest overall (0.88) of 1 ranked skill(s).
```

A skill that is not ranked has `-` as its rank and `overall`, and for each dimension without a score. `Ranking.notes` come first under `Notes:`, then each skill's.

`markdown(meta, ranking)` gives the human-readable report: the run's settings, this table, then a section per skill with its lift, dimension statuses, tests, clarity ratings, findings and local warnings, leaving out what is empty. It has the same fields as the JSON report (evaluate.md 2.1). Local warnings are headed as this machine's, to remove before sharing.

## 6. Untrusted text

Skill names come from the skill's own frontmatter, finding locations from its file names, and status details from backends such as Docker or a scanner. None of it is trusted. `judge.clean` ([judge.md](judge.md) 2.3), the same cleaner the judge uses for its own reasons, makes it one line, removes control characters (including terminal escape codes) and hidden characters, and cuts it to a length: 100 characters for a name (`MAX_NAME_CHARS`), 200 for the rest (`MAX_TEXT_CHARS`). `table` also escapes `|` in the name, so a skill cannot add a row of its own or spoof another skill's place. Every name, location and detail in the notes and the recommendation is cleaned the same way. A name with nothing left once cleaned is shown as `(unnamed)` (`UNNAMED`).

`table`'s output may end up rendered as Markdown, e.g. in `ranked-evaluation.md`, where a name like `![](https://attacker.example/x)` would make the viewer contact that server. So `table` escapes, with a backslash, the Markdown that can fetch or run something: `[` and `]` (links and images), `<` and `>` (HTML and autolinks), `` ` `` (code), and `\` itself, so a name cannot undo an escape. This applies to names, notes and the recommendation, and `markdown` does the same, after `clean`, for everything else in the report that came from a skill, a run, a backend or the judge. `_` and `*` are left alone: they only change formatting, and escaping them would show as `my\_skill` in a terminal. The notes' own wording uses none of the escaped characters, so only text from the skill or a backend changes. `Ranking` and `RankedSkill` keep the unescaped (but cleaned) text, for the JSON report, which needs no escaping.

## 7. Testing

[`tests/test_rank.py`](../../tests/test_rank.py) builds `SkillResult`s directly. It covers the default weights and their order of priority, the weighted mean, weight validation (also after the config was made), ordering and every tie-break, shared ranks, that the order does not depend on input order, unranked skills, their notes and that their scores are not averaged, no winner when every skill is incomplete, partial safety flagged only when statuses differ, CRITICAL findings, the lift warning, the recommendation and the table, and names and notes that try to add rows, send escape codes or inject Markdown links, images or HTML.
