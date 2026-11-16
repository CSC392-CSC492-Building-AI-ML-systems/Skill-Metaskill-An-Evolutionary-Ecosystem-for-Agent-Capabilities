# Clarity pilot

Status: draft, synthetic, not independently reviewed. No reviewed live judge runs or calibrated thresholds exist yet.

This pilot checks that the Clarity judge orders a clear skill above its deliberately unclear variant. It is not a benchmark of real-world skill clarity. The rubric, cases and live procedure are described in [evaluate-clarity.md](../../../../../docs/evaluate-clarity.md), section 4.

## Contents and boundaries

- `skills/<name>/`: ten clear skills (`clarity-high-*`) and ten unclear variants named `<name>-unclear` (`clarity-low-*`). Each is a SKILL.md with Hermes front matter and nothing else.
- `cases.json`: draft gold annotations, not runtime report objects. Clear cases annotate all seven criteria in `metaskill/evaluate/clarity.py`; each variant names its twin in `variant_of` and annotates only its `targeted_criteria`. Every expectation quotes text that appears verbatim in its skill.

Annotations live outside the skill folders, so a judge reading a skill never sees its label. The comparison in [clarity.py](../../clarity.py) is evaluator-owned and must not be given to the judged agent.

## First review

An independent teammate checks that each annotation's quote supports its expected properties, and that each unclear variant degrades only its targeted criteria. Record reviewer, date and disagreements before marking this approved gold. Do not copy judge ratings back into the annotations as independent labels.

## Live check

From the repository root:

1. `py -m metaskill.evaluate.golden.clarity --command` prints `/metaskill rank` with all twenty skills.
2. Run it in Hermes; the report folder is written under `evaluation-reports/`.
3. `py -m metaskill.evaluate.golden.clarity evaluation-reports/<run>/ranked-evaluation.json` prints one row per pair and exits non-zero unless every clear skill has the higher Clarity score and a strictly higher rating on every targeted criterion.

Offline verification is `python -m pytest tests/test_golden_clarity.py`. It checks annotation consistency, not judge behaviour.
