# Skill Evaluation — Design

Status: draft. Items marked **[OPEN]** need a decision before implementation.

Code lives in `metaskill/evaluate/`. This is Part 2 of the plugin.

## 1. Purpose

Given one or more Skills, produce a comparable, explainable score for each, and rank all skills against each other.

Evaluation is based off the following critereon:

- **Quality**: "Does this skill fulfill and/or exceed expectations with regards to its function?"
- **Robustness**: "Does this skill produce similar results across multiple usages?"
- **Clarity**: "How clear is the skill to the agent and/or user?"
- **Configuration Cleanliness**: "How easy is the skill to setup? Is the skill's configuration as simple as it needs to be?"
- **Efficiency**: "How efficient is the skill with respect to token use/time/resources?"
- **Safety** : "Does this skill do tasks which may pose a danger to the user?"

### Non-goals

- Not a general LLM benchmark. We score skills, not the underlying model.
- Not a new security scanner. Safety reuses an external scanner plus a few checks of our own (see [evaluate-safety.md](evaluate-safety.md)). PII detection belongs to `generalize`; evaluation only consumes its output as a signal.

## 2. Inputs and outputs

**Input:** a comma separated list of skills (directories containing skill.md alongside any required files).

**Output:** a table in the terminal interface which resembles the following:

| Rank | Skill | Overall | Quality | Robustness | Clarity | Config Cleanliness | Efficiency | Safety |
|---|---|---|---|---|---|---|---|---|
| 1 | gcp-file-transfer | 0.86 | 0.90 | 0.85 | 0.80 | 0.90 | 0.85 | 0.95 |
| 2 | s3-sync | 0.78 | 0.80 | 0.75 | 0.85 | 0.70 | 0.80 | 0.80 |
| 3 | log-cleanup | 0.61 | 0.70 | 0.60 | 0.65 | 0.80 | 0.75 | 0.30 |

Furthermore, calls to the skills `evaluate` and `rank` will be stored in their own files, with further details, as outlined in section 2.1.

*Rules:*
- We will require evidence (output of the tests), which will be outputted to a file
- Similarly, this data should be persistent. Each call writes two files:
  - `ranked-evaluation-[timestamp].json`: the source of truth, read by `rank.py` and later comparisons.
  - `ranked-evaluation-[timestamp].md`: a human-readable report rendered from the JSON.
- `[timestamp]` is simply the time where the skill was evoked (written as YYYY-MM-DD-T(Hour)-Minute-Seconds).

### 2.1 Report format **[OPEN]**

This is a temporary structure for the file format. Changes might be made to this (notably, rating needs to be changed in accordance to section 3.3 with regards to clarity.)

The structures below are defined in `models.py`. The Markdown report presents the same fields.

**Header (`meta`).** Ranking is only valid across identical settings (section 5), so every report records them:

| Field | Description |
|---|---|
| `timestamp` | Time of invocation, same format as the filename |
| `skills` | Skills evaluated, in input order |
| `runs_per_task` | `n` (default 3) |
| `judge_model` | Model used by `judge.py` |
| `safety_tier` | `static` or `deep` |
| `scanner_version` | Version of the external safety scanner |

**Per skill.** Name, the six dimension scores plus `overall`, `lift` (section 4.1), and three lists: `tests`, `rubric` and `findings`.

**Test results (`tests`).** One record per task, per variant, per run. Each record feeds Quality, Robustness and Efficiency at once, so it is not tagged with a dimension.

| Field | Description |
|---|---|
| `task_id` | Stable task identifier, so results can be compared across reports |
| `description` | One-line summary of the task |
| `variant` | `with_skill` or `baseline`; needed to compute lift |
| `run` | Run index `1..n`; needed to compute robustness |
| `judge` | `deterministic` or `llm` (section 4.2) |
| `passed` | Pass/fail; used for pass rate and lift |
| `score` | Score in `[0, 1]` |
| `reason` | Why the judge gave this score |
| `evidence` | For LLM judging, the quoted text supporting the rating; otherwise `null` |
| `metrics` | `tokens`, `seconds`, `tool_calls`; used for efficiency |
| `output` | Agent output, truncated, with a path to the full log |

**Clarity ratings (`rubric`).** One record per rubric criterion (section 3.3). The Clarity score is computed from these.

| Field | Description |
|---|---|
| `criterion` | Rubric criterion, e.g. `trigger_specificity`, `step_ordering` |
| `rating` | Anchored level for this criterion, normalised to `[0, 1]` |
| `reason` | Why the judge chose this level |
| `quote` | Text from the skill supporting the rating |

**Findings (`findings`).** Safety and Configuration Cleanliness do not run tasks; they report problems found in the skill's files instead.

| Field | Description |
|---|---|
| `dimension` | Dimension the finding counts towards |
| `severity` | `low`, `medium`, `high` or `critical` |
| `rule` | Identifier of the check that fired |
| `quote` | The offending text |
| `location` | File and line, e.g. `SKILL.md:42` |

Example (abridged):

```json
{
  "meta": {
    "timestamp": "2026-10-14-T15-30-02",
    "skills": ["gcp-file-transfer", "s3-sync"],
    "runs_per_task": 3,
    "judge_model": "...",
    "safety_tier": "static",
    "scanner_version": "..."
  },
  "skills": [{
    "name": "gcp-file-transfer",
    "scores": { "overall": 0.86, "quality": 0.90, "robustness": 0.85, "clarity": 0.80,
                "config_cleanliness": 0.90, "efficiency": 0.85, "safety": 0.95 },
    "lift": 0.40,
    "tests": [{
      "task_id": "upload-single-file",
      "description": "Upload ./a.txt to bucket X",
      "variant": "with_skill",
      "run": 1,
      "judge": "deterministic",
      "passed": true,
      "score": 1.0,
      "reason": "Object exists in bucket, exit code 0",
      "evidence": null,
      "metrics": { "tokens": 4210, "seconds": 18.2, "tool_calls": 5 },
      "output": "... (full log: logs/upload-single-file-with_skill-r1.txt)"
    }],
    "rubric": [{
      "criterion": "trigger_specificity",
      "rating": 0.75,
      "reason": "Names the service and action, but not when to prefer it over gsutil directly",
      "quote": "Use this skill to transfer files to and from GCP buckets."
    }],
    "findings": [{
      "dimension": "safety",
      "severity": "high",
      "rule": "destructive-without-confirm",
      "quote": "gsutil rm -r gs://...",
      "location": "SKILL.md:42"
    }]
  }]
}
```

- **[OPEN]** Maximum length of `output` before truncation, and where full logs are stored.

## 3. Scoring dimensions

Each criterion from the Purpose is scored in `[0, 1]` with supporting evidence. Cheap static checks run always; the rest are opt-in by budget.

| Dimension | Method | Cost | Signals |
|---|---|---|---|
| **Quality** | Run test tasks, with vs. without the skill | high | Task pass rate and lift over baseline |
| **Robustness** | Repeat the same tasks `n` times | high | Agreement of outcomes across runs |
| **Clarity** | LLM judge against a rubric | low | Specific trigger, ordered and actionable steps, failure handling |
| **Configuration Cleanliness** | Static + LLM | low | Number and necessity of config entries, documented placeholders, setup steps |
| **Efficiency** | Measured during task runs | medium | Tokens, wall-clock time, tool calls per task |
| **Safety** | External scanner + own checks; optional LLM tier | none (static), low (deep) | Malicious content, destructive actions without confirmation, credentials printed or logged, untrusted sources, excess scope |

Quality, Robustness and Efficiency share one set of task runs (section 4), so they cost one execution pass, not three.

### 3.1 Quality

Does the skill do its job? Run each test task with the skill and without it. Quality combines the with-skill pass rate and the lift over baseline, so a skill that succeeds only because the task was easy scores lower than one that changes the outcome. Task sources and judging are in section 4.

### 3.2 Robustness

Does the skill behave consistently? Run each task `n` times (default 3) and measure agreement between runs: the fraction of tasks with the same pass/fail result, plus the spread of the quality scores. A skill that passes 3/3 or fails 3/3 is robust; one that passes 1/3 is not, even if its mean pass rate looks acceptable.

### 3.3 Clarity (LLM judge)

Fixed rubric with anchored levels per criterion: trigger specificity, step ordering, verifiability of steps, handling of failure cases, and whether a human reader could follow it as well as an agent. The judge must quote the text supporting each rating.

### 3.4 Configuration Cleanliness

How hard is it to get the skill working, and is that effort necessary? Signals:

- Number of required config entries, and whether each has a description and a default or example.
- Placeholders (`{{...}}`) that map to a documented config entry count **positively**. This is what `generalize` produces.
- Penalties for hardcoded paths, usernames, hosts, or credentials (reuses `generalize` detection), for undocumented placeholders, and for config that the skill body never uses.
- An LLM check for setup steps that could be simplified or removed.
- **[OPEN]** Whether this dimension also consumes the safety scanner's `hardcoded_secrets` and `policy_violation` findings, which are cleanliness issues, not safety ones. See [evaluate-safety.md](evaluate-safety.md), section 2.

### 3.5 Efficiency

Measured from the task runs in section 4, not estimated from the text: tokens consumed, wall-clock time, and tool calls. Scores are relative to the baseline run (without the skill) on the same task, so a skill that makes the agent slower or costlier than having no skill is penalized. Static size (length of `SKILL.md`, bundled file size) is a minor secondary signal because it is paid on every load.

### 3.6 Safety

Does the skill ask the agent to do something that could harm the user? Two layers: an external scanner (Cisco skill-scanner) for malicious skills, such as prompt injection, data exfiltration and malicious code, and our own checks for careless ones:

- Destructive or irreversible commands (recursive deletes, force pushes, disk or database writes) without a confirmation step.
- Credentials printed or logged at run time.
- Installs or downloads from unpinned or unknown sources.
- Broad permissions, or actions with side effects outside the stated purpose.

The default `static` tier needs no API key; the `deep` tier adds LLM passes. Each finding has a severity and quotes the offending text. A scan with no findings does not prove a skill is safe. Full design in [evaluate-safety.md](evaluate-safety.md).

## 4. Task Execution

### 4.1 Approach

Run the same task with and without the skill, and score the difference. The skill's value is the **lift**, not the absolute pass rate.

```
lift = pass_rate(with_skill) - pass_rate(baseline)
```

A skill with high pass rate but zero lift is not adding value.

### 4.2 Judging

Prefer, in order:

1. **Deterministic checks** (file exists, command exit code, output matches): easy to program/verify, cheap.
2. **LLM judge with a task-specific checklist**: when outcomes are not deterministic or easily machine verifiable.

### 4.3 Where test tasks come from.
All tasks will be generated by humans, with the assistance of AI tools.

There will be no tasks which are generated on the fly, to avoid adding extra variance to tests.

### 4.4 Variance

With regards to both LLM-based judgement and certain scoring categories, there is a need to account for variance (due to the non-deterministic answers of the agent). 

To account for this, tests will be ran `n` times (we default to 3), and account for the mean and spread of the data. We may consider lengthening the confidence of a score if needed.

## 5. Ranking

- Rank by `overall`, with ties broken by efficacy lift, then safety, followed by quality, robustness, clarity, etc...
- Only rank skills evaluated with the **same config, judge model, safety tier and scanner version**; otherwise refuse or warn, since scores are not comparable.
- A critical safety finding results in a low safety score. It does not remove the skill from the ranking.
- Output includes as well, a recommendation of which score to use based off their scores.


## 6. Module layout

This is the current planned file structure/layout in the evaluate folder. This may change in the future.

```
metaskill/evaluate/
  __init__.py        # public API: evaluate_skill(), rank_skills()
  models.py          # Evaluation data structures (scores, evidence, config)
  judge.py           # General LLM judge (interface for the judge model)

  runner.py          # Task runner: executes tasks n times, with and without
                     #   the skill; records outcomes, tokens, time, tool calls
  tasks.py           # Test task loading (author-provided) and generation (4.3)

  quality.py         # 3.1: pass rate and lift, from runner results
  robustness.py      # 3.2: agreement across the n runs, from runner results
  clarity.py         # 3.3: rubric-based LLM judging via judge.py
  config_cleanliness.py  # 3.4: config entries and placeholders; reuses generalize detectors
  efficiency.py      # 3.5: tokens/time/tool calls vs. baseline, from runner results
  safety/            # 3.6: scanner adapter, own checks, LLM review
                     #   (layout in evaluate-safety.md)

  rank.py            # Overall score, comparison and ordering
```

Dependencies:

- `runner.py` is the only module that executes the agent. `quality.py`, `robustness.py` and `efficiency.py` are pure functions over its results, so they share one execution pass and are testable without an LLM.
- `clarity.py`, `safety/` and the LLM part of `config_cleanliness.py` call `judge.py`, never a model directly.
  - Exception: the safety scanner's LLM analyzer makes its own call, configured with the judge's provider and model. See [evaluate-safety.md](evaluate-safety.md), section 4.
- Each scoring module exposes the same shape (`score(skill, ...) -> DimensionScore` from `models.py`), so `rank.py` treats all six uniformly.


## 7. Tool surface

Exposed through the existing `metaskill` tool (`request` string), e.g. "evaluate gcp-file-transfer" or "rank my skills". Requests are routed to `evaluate_skill` / `rank_skills`. 

## 8. Testing

- Structure checks: fixture skills in `tests/` (one valid, several each broken in one specific way).
- Judge and efficacy: fake judge returning canned output; assert report shape, null handling, and score aggregation.
- Ranking: property tests (ordering is stable, same-input-same-output, incomparable configs rejected).
- Real LLM runs are excluded from CI (`pr-checks.yml` runs pytest only) and marked separately.

## 9. Limitations

- LLM judges are biased toward verbose, well-formatted text. Rubric anchors and mandatory quote-evidence mitigate but do not remove this.
- Certain categories are dependent on the quality of the task (i.e. robustness).
- Scores are only comparable within one config and judge model. Multiple different judges may cause noise to appear in scoring.
- Cost scales with `tasks × runs × 2` (baseline and with-skill) in worst case, with the average being `tasks x runs`. May want to improve further using some other scoring system.

## 10. Open questions

1. Task sources and weighted scores against only AI tests. (4.3).
2. Weighted-mean `overall`: Is it the best scoring system?
3. The structuring and eventual scoring of generalized skills; whether we can accurately create tests surrounding these skills at this stage in development (week 4).
4. Limitations on persistance of skill rankings.