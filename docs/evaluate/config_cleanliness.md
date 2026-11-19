# Configuration Cleanliness: pilot specification

Status: draft for independent review. Owner: Configuration Cleanliness workstream.

## Purpose and boundary

Measure how understandable and necessary setup is, whether declarations match usage, and whether reusable logic separates configuration appropriately. This is not a new PII detector. Reuse generalization findings when that component is available, and distinguish unavailable detection from a clean result.

[The overview](../evaluate.md) owns the metric intent; [the scoring draft](../evaluation/scoring.md#configuration-cleanliness) proposes four properties and their aggregation. No weights or numeric anchors are approved by these fixtures.

## Evidence and model integration

Inputs are the skill bundle, configuration/schema where provided, declared transformation stage, detector findings, reviewed expectations, and declared values that must vary across users or profiles. The current Skill object has no config-schema field; loading configuration and detector output needs an explicit adapter contract.

Use deterministic evidence for missing descriptions, schema fields, and known fixture occurrences. Contextual necessity and semantic usage may need judge.py; do not infer arbitrary script behaviour from a simple substring search. Findings should use the existing Finding/DimensionScore shape. Final category/rule identifiers and severity-to-score policy require review before implementation.

Never expose raw detected secrets in explanations. Pilot values are fictitious non-secret organization labels.

## Four proposed properties

| Property | Evidence question |
| --- | --- |
| Necessary setup | Does each required entry or setup step serve the intended function? |
| Documentation | Are required values, formats, and safe examples or defaults explained? |
| Configuration integrity | Do declared configuration and actual consumers agree for this stage? |
| Separation | Are values that should vary between users appropriately configurable? |

The draft proposes equal-weight ratings; this pilot stores properties and defects, not preassigned numeric skill scores. For schema-free skills, check setup instructions, examples, and documented environment variables. Weighting review should consider whether a confirmed separation failure should cap the score when hardcoding bypasses configuration entirely.

## Pilot variants

The shared [pilot annotations](../../metaskill/evaluate/golden/synthetic/ticket-summary/configuration_expectations.json) remain draft until another teammate reviews them.

| Variant | Intended evidence |
| --- | --- |
| reference | One required documented organization label read from config.json. |
| hardcoded | Organization is fixed to Example Alpha, contrary to this family's alternate-user requirement. |
| undocumented | Required schema field lacks a description. |
| unused | legacy_region is declared and supplied but has no consumer in this instruction-only workflow. |

All variants have the same core reporting task. A configuration defect does not guarantee lower live Quality under the Alpha profile. Beta tests whether the hardcoded limitation affects runtime Quality. The public prompt gives supplied configuration precedence, so an agent can still pass by following the prompt. Static separation evidence comes from the declared profile variability.

## Stages and transformation handoff

These are configured skills, not approved generalized outputs. Declared unresolved placeholders are expected in generalized templates; actual supplied values are expected after personalization. Intentionally retained ambiguous detector findings do not automatically fail separation.

The hardcoded occurrence can seed an approved generalization decision. Expect schema/example generation, declared placeholder substitution, and Beta personalization once the transformation pipeline exists. Do not claim this round trip has run.

## Missing evidence and next decisions

If the detector is unavailable, report the limit explicitly; do not conclude that no residual personal data exists. Decide which evidence subsets justify a numeric partial score and which require null before implementing the scorer. Unknown necessity is not automatically unnecessary setup.

Agree the adapter, finding categories, unavailable-backend handling, rating anchors, and weighting with the team. Safety scanner category routing stays owned by the existing safety/configuration integration discussion; this pilot does not introduce a second safety score.

Offline tests check annotation references and synthetic profile semantics. They do not implement or validate a general-purpose configuration-cleanliness scorer.

## Current evidence ownership and scoring

The Configuration Cleanliness workstream owns the adapter that supplies reviewed
variability and usage evidence. The evaluator currently supplies pinned evidence
only for the bundled ticket-summary pilot. Supporting arbitrary skills requires
a separate adapter with declared input coverage and provenance.

A usage inventory must explicitly declare completeness through usage_complete.
Partial inventories provide positive usage evidence only. Completeness is an
assertion by the evidence producer and requires review or a supported analysis
method covering the entire bundle. The checker cannot establish completeness
from a set of names alone.

Only findings produced by config-checks participate in this scorer's decisions.
Cisco findings routed to Configuration Cleanliness remain report evidence.
A confirmed Separation failure scores zero. The pinned reference scores one
when its complete reviewed inputs match. Other incomplete cases remain null.
Schema references and compositions remain unassessed by the description check.

## MET-59 evidence discovery

The normal evaluation entry point collects Configuration Cleanliness evidence for
arbitrary skill bundles using the existing generalization inventory, detectors,
and literal config placeholder pattern. Evidence uses the source config-evidence
and records relative file names and line numbers. Detected values and credential
contents are omitted from finding quotes and messages.

Supported sources are Markdown, plain text, Python, shell, JavaScript, and
TypeScript files accepted by the generalization inventory. Literal
{{config.key}} references are recorded using the same syntax as personalization.
Environment-variable references and embedded-value candidates use the existing
generalization detectors. Configuration JSON, schema files, and other data files
are excluded from embedded instruction checks, since supplied configuration is
expected to contain concrete values.

These observations have INFO severity and provide partial coverage. An embedded
value is a candidate for review of required variability. It does not prove a
Separation failure. References can occur in examples, dead code, or optional
branches, and dynamic accesses can escape detection. The adapter therefore never
declares semantic usage complete, never infers unused settings from missing
references, and never independently awards a numeric score.

Inventory limits and skipped files remain visible in the dimension detail.
Reported observations are capped at 1,000 per skill. Existing reviewed pilot
evidence and explicit complete inventories retain their MET-56 behavior.

The generalization pipeline currently returns detection counts rather than a
persistent reviewed decision artifact. MET-59 reuses its deterministic detectors
directly. Consuming independently reviewed decisions with source bindings and
establishing complete usage for additional patterns remain adapter extensions.

### Processing bounds and detector coverage

MET-59 now bounds work before calling the eager generalization detector. A
supported source over 64 KiB is skipped in full, and at most 256 KiB of supported
source text is passed to placeholder and detector analysis per skill. The detail
reports the number of files skipped by these limits. Inventory discovery retains
its existing bounded read limits; the 1,000-observation cap additionally bounds
the report. Skipped analysis always leaves usage coverage partial.

The embedded-value-needs-review rule forwards REPLACE and REVIEW candidates
actually emitted by generalization's deterministic detectors. These cover
recognized credential patterns and secret assignments, repository identifiers,
email addresses, phone numbers, machine-specific paths, organization hosts, and
configuration-like assignments. The detectors retain their existing context,
example-value, public-value, and confidence filters. A value appearing in prose
or resembling a provider region or credential does not guarantee a finding.
Environment references classified KEEP are reported separately as references.
This adapter adds no independent PII classifier, and absence of candidates
provides no clean Separation verdict.
