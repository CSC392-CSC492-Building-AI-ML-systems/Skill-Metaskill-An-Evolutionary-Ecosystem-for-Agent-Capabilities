# Quality: pilot specification

Status: draft for independent review. Owner: Quality metric workstream.

## Purpose

Quality measures task success and added value relative to the same agent without the skill. [The overview](../evaluate.md) defines this intent; [the scoring draft](../evaluation/scoring.md#quality) proposes, but does not finalize, its formula. No new Quality implementation or formula approval is claimed here.

## Inputs and outputs

The planned scorer consumes complete paired TestResult records, grouped by task_id, variant, and run. It is a pure function over recorded outcomes, not an agent runner or judge. Its output should be DimensionScore for quality; the report also needs pass rates and lift with explanatory evidence. The final function signature and how supporting statistics reach SkillResult require agreement with the runner/report owners.

Use the same tasks, full success contract, configuration, model, tools, limits, and repetition counts in both conditions. Do not give the skill hidden task requirements and then interpret the information advantage as general skill quality.

## Pilot task and checks

[The ticket-summary pilot](../../metaskill/evaluate/golden/synthetic/ticket-summary/README.md) has eight hand-authored input scenarios, explicitly scheduled across five candidate variants and two profiles (80 logical cases), covering totals, cancellation, sorting, empty lists, invalid types, and exact team names. Expected JSON is distinct from runtime output and remains evaluator-only.

Its trusted semantic verifier accepts object-key reordering but rejects wrong counts/totals, missing or extra output fields, duplicate keys, Boolean/integer substitution, and explanatory text outside the JSON. Each case currently has one all-or-nothing content assertion: pass yields task score 1, fail yields 0. This pilot does not adopt the scoring draft's richer partial-credit proposal.

Source preservation and network restrictions require separate runtime observations. Correct JSON alone does not establish either.

## Calculation proposal

For each task, average accepted pass indicators across its planned repetitions. Then average across tasks equally, separately for with_skill and baseline. Lift is the difference between the two rates, in [-1, 1].

The current draft formula is Q = P_skill * (0.75 + 0.25 * lift). Retain it only as a proposal for review. It yields Q=1 when skill passes everything and baseline fails everything; Q=0.75 when both pass everything; Q=0 when the skill never passes. Ask whether the reduced score at zero lift captures the team's intended meaning.

This pilot can produce zero lift on a capable agent. Do not weaken the baseline to manufacture a positive result.

## Availability and validation

Missing logical runs after bounded infrastructure retries leave Quality unscored with a reason. A valid failed task is a zero, not missing evidence. Missing or duplicate run identities, unequal task sets, and incompatible settings must be identified before computing a comparable score.

Before implementation, agree whether absent baseline permits absolute success reporting without a Quality score; do not silently assume zero baseline success. Validate scoring on recorded all-pass, all-fail, positive/negative-lift, and invalid/incomplete run matrices once the interface is agreed.

The offline pilot tests validate gold consistency, verifier behaviour, and an injected semantic judge through the existing loader and runner with inert agent/sandbox doubles. They do not measure agent pass rates, lift, robustness, or Quality scores. Human review and live runs remain pending.

## Runner integration milestone

The pilot now prepares real Task/Check-compatible task files with safe runtime IDs and uses an evaluator-owned TaskJudge injected through run_tasks. Expected outputs stay outside staged agent fixtures. See [the adapter procedure](../../metaskill/evaluate/golden/synthetic/ticket-summary/README.md#integration-status).

This preserves the shared report models and runner implementation. Tests verify accepted and failed TestResult records and logs; Quality aggregation is still not implemented. Live execution still requires a compatible Agent and the runner's sandbox.
