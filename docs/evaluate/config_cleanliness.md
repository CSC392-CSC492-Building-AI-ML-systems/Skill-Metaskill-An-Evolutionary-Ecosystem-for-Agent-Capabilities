# Configuration Cleanliness: pilot specification

Status: draft for independent review. Owner: Configuration Cleanliness workstream.

## Purpose and boundary

Measure how understandable and necessary setup is, whether declarations match usage, and whether reusable logic separates configuration appropriately. This is not a new PII detector. Reuse generalization findings when that component is available, and distinguish unavailable detection from a clean result.

[The overview](../evaluate.md) owns the metric intent; [the scoring draft](../evaluation/scoring.md#configuration-cleanliness) proposes four properties and their aggregation. No weights or numeric anchors are approved by these fixtures.

## Evidence and model integration

Inputs are the skill bundle, configuration/schema where provided, declared transformation stage, detector findings, and relevant reviewed expectations. The current Skill object has no config-schema field; loading configuration and detector output needs an explicit adapter contract.

Use deterministic evidence for missing descriptions, schema fields, and known fixture occurrences. Contextual necessity and semantic usage may need judge.py; do not infer arbitrary script behaviour from a simple substring search. Findings should use the existing Finding/DimensionScore shape. Final category/rule identifiers and severity-to-score policy require review before implementation.

Never expose raw detected secrets in explanations. Pilot values are fictitious non-secret organization labels.

## Four proposed properties

| Property | Evidence question |
| --- | --- |
| Necessary setup | Does each required entry or setup step serve the intended function? |
| Documentation | Are required values, formats, and safe examples or defaults explained? |
| Configuration integrity | Do declared configuration and actual consumers agree for this stage? |
| Separation | Are values that should vary between users appropriately configurable? |

The draft proposes equal-weight ratings; this pilot stores properties and defects, not preassigned numeric skill scores. A missing schema alone is not universally a defect if a skill documents another valid interface.

## Pilot variants

The shared [pilot annotations](../../metaskill/evaluate/golden/synthetic/ticket-summary/configuration_expectations.json) remain draft until another teammate reviews them.

| Variant | Intended evidence |
| --- | --- |
| reference | One required documented organization label read from config.json. |
| hardcoded | Organization is fixed to Example Alpha, contrary to this family's alternate-user requirement. |
| undocumented | Required schema field lacks a description. |
| unused | legacy_region is declared and supplied but has no consumer in this instruction-only workflow. |

All variants have the same core reporting task. A configuration defect does not guarantee lower live Quality under the Alpha profile. Beta exposes the hardcoded reuse limitation without treating all embedded organization names in all skills as forbidden.

## Stages and transformation handoff

These are configured skills, not approved generalized outputs. Declared unresolved placeholders are expected in generalized templates; actual supplied values are expected after personalization. Intentionally retained ambiguous detector findings do not automatically fail separation.

The hardcoded occurrence can seed an approved generalization decision. Expect schema/example generation, declared placeholder substitution, and Beta personalization once the transformation pipeline exists. Do not claim this round trip has run.

## Missing evidence and next decisions

If the detector is unavailable, report the limit explicitly; do not conclude that no residual personal data exists. Decide which evidence subsets justify a numeric partial score and which require null before implementing the scorer. Unknown necessity is not automatically unnecessary setup.

Agree the adapter, finding categories, unavailable-backend handling, rating anchors, and weighting with the team. Safety scanner category routing stays owned by the existing safety/configuration integration discussion; this pilot does not introduce a second safety score.

Offline tests check annotation references and synthetic profile semantics. They do not implement or validate a general-purpose configuration-cleanliness scorer.
