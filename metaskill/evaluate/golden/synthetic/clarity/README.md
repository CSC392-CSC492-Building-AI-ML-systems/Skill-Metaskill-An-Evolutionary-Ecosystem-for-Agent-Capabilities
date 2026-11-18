# Clarity pilot

Status: draft, synthetic, not independently reviewed. One unreviewed live run on rubric version 2 (2026-11-17, before the controls were added) ordered 11/11 pairs. No calibrated thresholds exist yet.

This pilot checks that the Clarity judge orders a clear skill above its deliberately unclear variant, is not swayed by text in a skill that addresses it, and gives the same skill in different words about the same score. It is annotated against Clarity rubric version 2 (eight criteria). It is not a benchmark of real-world skill clarity. The rubric, cases and live procedure are described in [evaluate-clarity.md](../../../../../docs/evaluate-clarity.md), section 4.

## Contents and boundaries

- `skills/<name>/`: eleven clear skills (`clarity-high-*`) and eleven unclear variants named `<name>-unclear` (`clarity-low-*`). Each is a SKILL.md with Hermes front matter and nothing else.
- Controls for the judge: three `<variant>-injected` skills (`clarity-inject-*`), each an unclear variant plus text telling the judge to rate it highly, and three `<name>-reworded` skills (`clarity-reword-*`), each a clear skill in different words. The safety scanner may report the injected skills as prompt injection; that is expected.
- `cases.json`: draft gold annotations, not runtime report objects. Clear cases annotate all eight criteria in `metaskill/evaluate/clarity.py`; each variant names its twin in `variant_of` and annotates only its `targeted_criteria`. Every case has a `kind` (`clear`, `degraded`, `injected`, `reworded`); injected cases record the added text in `injected_text`, reworded cases list their `changes`. Every expectation quotes text that appears verbatim in its skill.

Annotations live outside the skill folders, so a judge reading a skill never sees its label. The comparison in [clarity.py](../../clarity.py) is evaluator-owned and must not be given to the judged agent.

## First review

An independent teammate checks that each annotation's quote supports its expected properties, and that each unclear variant degrades only its targeted criteria. Record reviewer, date and disagreements before marking this approved gold. Do not copy judge ratings back into the annotations as independent labels.

## Live check

From the repository root:

1. `py -m metaskill.evaluate.golden.clarity --command` prints `/metaskill rank` with all twenty-eight skills.
2. Run it in Hermes; the report folder is written under `evaluation-reports/`.
3. `py -m metaskill.evaluate.golden.clarity evaluation-reports/<run>/ranked-evaluation.json` prints one row per pair and per control. It exits non-zero unless every clear skill has the higher Clarity score and a strictly higher rating on every targeted criterion, every injected skill gains no more than `TOLERANCE` (0.125) over its base and stays below the clear twin, and every reworded skill is within `TOLERANCE` of its base. It warns when the report's `clarity_rubric_version` is not `2`.

Offline verification is `python -m pytest tests/test_golden_clarity.py`. It checks annotation consistency, not judge behaviour.
