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
- Not a security scanner. PII detection belongs to `generalize`; evaluation only consumes its output as a signal.

## 2. Inputs and outputs

**Input:** a comman separated list of skills (directories containing skill.md alongside any required files).

**Output:** a table in the terminal interface which resembles the following:

| Rank | Skill | Overall | Quality | Robustness | Clarity | Config Cleanliness | Efficiency | Safety |
|---|---|---|---|---|---|---|---|---|
| 1 | gcp-file-transfer | 0.86 | 0.90 | 0.85 | 0.80 | 0.90 | 0.85 | 0.95 |
| 2 | s3-sync | 0.78 | 0.80 | 0.75 | 0.85 | 0.70 | 0.80 | 0.80 |
| 3 | log-cleanup | 0.61 | 0.70 | 0.60 | 0.65 | 0.80 | 0.75 | 0.30 |

## 3. Scoring dimensions

Each criterion from the Purpose is scored in `[0, 1]` with supporting evidence. Cheap static checks run always; the rest are opt-in by budget.

| Dimension | Method | Cost | Signals |
|---|---|---|---|
| **Quality** | Run test tasks, with vs. without the skill | high | Task pass rate and lift over baseline |
| **Robustness** | Repeat the same tasks `n` times | high | Agreement of outcomes across runs |
| **Clarity** | LLM judge against a rubric | low | Specific trigger, ordered and actionable steps, failure handling |
| **Configuration Cleanliness** | Static + LLM | low | Number and necessity of config entries, documented placeholders, setup steps |
| **Efficiency** | Measured during task runs | medium | Tokens, wall-clock time, tool calls per task |
| **Safety** | Static scan + LLM review | low | Destructive or irreversible actions, credential handling, unconfirmed external side effects |

Quality, Robustness and Efficiency share one set of task runs (section 4), so they cost one execution pass, not three.

### 3.1 Quality

Does the skill do its job? Run each test task with the skill and without it. Quality combines the with-skill pass rate and the lift over baseline, so a skill that succeeds only because the task was easy scores lower than one that changes the outcome. Task sources and judging are in section 4.

### 3.2 Robustness

Does the skill behave consistently? Run each task `n` times (default 3) and measure agreement between runs: the fraction of tasks with the same pass/fail result, plus the spread of the quality scores. A skill that passes 3/3 or fails 3/3 is robust; one that passes 1/3 is not, even if its mean pass rate looks acceptable.

### 3.3 Clarity (LLM judge)

Fixed rubric with anchored levels (1/3/5) per criterion: trigger specificity, step ordering, verifiability of steps, handling of failure cases, and whether a human reader could follow it as well as an agent. The judge must quote the text supporting each rating.

### 3.4 Configuration Cleanliness

How hard is it to get the skill working, and is that effort necessary? Signals:

- Number of required config entries, and whether each has a description and a default or example.
- Placeholders (`{{...}}`) that map to a documented config entry count **positively**. This is what `generalize` produces.
- Penalties for hardcoded paths, usernames, hosts, or credentials (reuses `generalize` detection), for undocumented placeholders, and for config that the skill body never uses.
- An LLM check for setup steps that could be simplified or removed.

### 3.5 Efficiency

Measured from the task runs in section 4, not estimated from the text: tokens consumed, wall-clock time, and tool calls. Scores are relative to the baseline run (without the skill) on the same task, so a skill that makes the agent slower or costlier than having no skill is penalized. Static size (length of `SKILL.md`, bundled file size) is a minor secondary signal because it is paid on every load.

### 3.6 Safety

Does the skill ask the agent to do something that could harm the user? A static scan plus an LLM review flag:

- Destructive or irreversible commands (recursive deletes, force pushes, disk or database writes) without a confirmation step.
- Credentials printed, logged, or sent to third parties.
- Network calls or installs from unpinned or unknown sources.
- Broad permissions, or actions with side effects outside the stated purpose.

Each finding has a severity and quotes the offending text. This is not a full security scanner (see non-goals); it reports risk visible in the skill's instructions. **[OPEN]** Whether a low Safety score should cap `Overall` (e.g. a hard ceiling when any high-severity finding exists) instead of being averaged in.

## 4. Efficacy evaluation

### 4.1 Approach

Run the same task with and without the skill, and score the difference. The skill's value is the **lift**, not the absolute pass rate.

```
lift = pass_rate(with_skill) - pass_rate(baseline)
```

A skill with high pass rate but zero lift is not adding value.

### 4.2 Judging

Prefer, in order:

1. **Programmatic checks** (file exists, command exit code, output matches): deterministic, cheap.
2. **LLM judge with a task-specific checklist**: when outcomes are not mechanically checkable.

### 4.3 Where test tasks come from  **[OPEN]**

Options:

- (a) Author-provided in `tests/` or alongside the skill (best quality, extra work).
- (b) LLM-generated from the skill's description (cheap, but risks tasks that mirror the skill's own wording).
- (c) Both: author-provided if present, else generated and flagged `confidence: low`.

Recommendation: (c).

### 4.4 Variance

Agent runs are non-deterministic. Run each task `n` times (default 3), report mean and spread, and widen `confidence` bounds when runs disagree.

## 5. Ranking

- Rank by `overall`, with ties broken by efficacy lift, then portability.
- Only rank skills evaluated with the **same config and judge model**; otherwise refuse or warn, since scores are not comparable.
- Output includes per-dimension deltas between adjacent ranks so the ranking is explainable.

For the merge loop: a child is "better" than its parents only if it beats both on `overall` **and** does not regress any single dimension by more than a tolerance (default 0.1). **[OPEN]** whether to use Pareto dominance instead.

## 6. Module layout

```
metaskill/evaluate/
  __init__.py      # public API: evaluate_skill(), rank_skills()
  models.py        # EvaluationReport, DimensionScore, EvalConfig
  structure.py     # deterministic checks
  judge.py         # LLM rubric judging (model-agnostic interface)
  portability.py   # shared with generalize detectors
  efficacy.py      # task runner, baseline vs. with-skill
  rank.py          # comparison and ordering
```

The LLM judge sits behind a small interface (`judge(prompt) -> str`) so tests can inject a fake and the plugin can use whatever model Hermes provides.

## 7. Tool surface

Exposed through the existing `metaskill` tool (`request` string), e.g. "evaluate gcp-file-transfer" or "rank my skills". Requests are routed to `evaluate_skill` / `rank_skills`. If a separate structured tool is wanted later, add an `evaluate` schema in `schemas.py`. **[OPEN]**

## 8. Testing

- Structure checks: fixture skills in `tests/` (one valid, several each broken in one specific way).
- Judge and efficacy: fake judge returning canned output; assert report shape, null handling, and score aggregation.
- Ranking: property tests (ordering is stable, same-input-same-output, incomparable configs rejected).
- Real LLM runs are excluded from CI (`pr-checks.yml` runs pytest only) and marked separately.

## 9. Limitations

- LLM judges are biased toward verbose, well-formatted text. Rubric anchors and mandatory quote-evidence mitigate but do not remove this.
- Efficacy depends on task quality; generated tasks can overstate results.
- Scores are only comparable within one config and judge model.
- Cost scales with `tasks × runs × 2` (baseline and with-skill).

## 10. Open questions

1. Task source for efficacy (4.3).
2. Default dimension weights. Proposal: structure 0.15, clarity 0.25, portability 0.20, efficacy 0.40.
3. Weighted-mean `overall` vs. Pareto comparison for the merge loop (5).
4. Do generated skills carry design metadata (intent, success criteria) in frontmatter that evaluation can use as its rubric, instead of inferring intent from the text?
5. Separate `evaluate` tool vs. routing through `metaskill` (7).
