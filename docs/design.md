# Skill Generalization and Personalization — Design

**Code lives in:** `metaskill/generalize/` and `metaskill/personalize/` (planned; these modules are not yet present).  
**Scope:** Part 1: Skill Generalization. The base Hermes plugin and `/metaskill` command are already set up.

## 1. Purpose

Given an existing Hermes skill, analyze its structure and contents—including `SKILL.md` and relevant supporting files—then identify user-specific configuration and PII. Separate those values from reusable skill logic, replace them with reusable configuration parameters or placeholders, and generate a reusable skill template. Given that generalized skill and a new user's configuration, perform the reverse transformation to produce a personalized skill while preserving the original behavior.

The tools should make skills easier to share without accidentally sharing the original author's credentials, personal details, or machine-specific settings.

### Non-goals

- Guarantee that every sensitive value in arbitrary files can be detected.
- Prove behavioral equivalence for all inputs or certify that a skill is safe.
- Execute skill code during inspection, generalization, or personalization.
- Publish or install generated skills automatically.
- Score, compare, or merge skills; those are separate project capabilities.

## Part 1 milestone outcomes

By the end of this milestone, a user should be able to install the Metaskill plugin through Hermes, select an existing skill, and generalize it into a reusable template while preserving its intended functionality. The milestone includes:

- Designing a reusable Hermes configuration template structure.
- Extending the existing `/metaskill` command and plugin tool schema to route generalization and personalization requests.
- Building a skill project parser that detects PII and configuration in `SKILL.md`, scripts, and other relevant files.
- Building the generalization pipeline and the reverse personalization transformation.
- Providing a tool schema that helps Hermes determine which operation to run.
- Testing generalization and personalization on real Hermes skills, including validation of functionality.
- Documenting detection accuracy, edge cases, security risks, and limitations.

## 2. Inputs and outputs

### Generalize

**Input:** A Hermes skill project, typically a directory containing `SKILL.md` and supporting files, or a standalone `SKILL.md` when no project files are needed.

**Output:** A new output directory containing the generalized skill, a configuration schema, a blank/example configuration file, and a findings report.

```text
output/
  generalized-skill/
    SKILL.md
    ...supporting files...
  config.schema.json
  config.example.json
  findings.json
```

The example configuration must not contain the detected source values. Findings should refer to file and location, category, confidence, and action without copying secret values into reports.

### Personalize

**Input:** A generalized skill, its configuration schema, and a user-supplied configuration file.

**Output:** A separate directory containing the personalized skill and a concise result report. The input skill and configuration remain unchanged. **[OPEN]** Whether the report should be a standalone file or only a Hermes tool response.

## 3. Generalization design

### 3.1 File inspection

Resolve the input path and define a bounded scan root. Parse the skill project and inspect `SKILL.md` plus relevant supporting files, including scripts. Extract prompts, metadata, tool declarations, and dependencies where present. Start with an explicitly allowlisted set such as Markdown, text, JSON, YAML, TOML, and common script formats. Do not execute files.

Ignore version-control internals, caches, generated output, and binary files by default. List unsupported files in the findings report. Reject or report symlinks and paths resolving outside the selected root. Apply file-count and size limits. **[OPEN]** Final extension allowlist and limits.

### 3.2 Detection and transformation

1. **Inventory:** Enumerate eligible files and record relative paths and file types.
2. **Deterministic detection:** Identify candidates using known credential patterns, API keys, emails, phone numbers, URLs, IP addresses, local/absolute paths, organization configurations, and common configuration assignments.
3. **Contextual classification:** Optionally use an LLM for ambiguous candidates, such as distinguishing an example address from an author's real address or identifying organization-specific settings. LLM classifications are advisory and must use validated structured output.
4. **Review:** Present candidate findings grouped by category and confidence. Leave ambiguous candidates unchanged when review is unavailable, and report them.
5. **Placeholder assignment:** Replace approved user-specific values with standardized, reusable configuration parameters. Repeated occurrences of the same value should share a placeholder when they serve the same purpose.
6. **Configuration generation:** Create a field for each placeholder with a name, type, required flag, description, and validation constraints where known.
7. **Output validation:** Confirm each placeholder has a configuration definition and that approved values were replaced. Run residual detection and report remaining candidates, without implying that a clean report proves complete sanitization.

Example categories and placeholder names:

| Category | Examples | Placeholder |
|---|---|---|
| Credential | API token, password, private key | `{{config.api_token}}` |
| Personal contact | Personal email or phone | `{{config.contact_email}}` |
| Local environment | Home directory or machine-specific path | `{{config.workspace_path}}` |
| Organization | Internal host, team name, tenant ID | `{{config.organization_host}}` |
| Deployment setting | Region, project or account ID | `{{config.region}}` |

Do not classify every URL, name, or path as private by default. Public documentation links, sample values, and portable relative paths may be reusable content. Preserve surrounding instructions and formatting, using exact scoped replacements rather than broad substitutions.

### 3.3 Configuration template format

Generate a machine-readable schema and a human-editable example. **[OPEN]** Confirm JSON versus YAML for the user-edited configuration; JSON is the initial example below.

```json
{
  "api_token": "",
  "contact_email": "",
  "workspace_path": "",
  "organization_host": "",
  "region": ""
}
```

The schema should identify sensitive fields so their values are excluded from logs and ordinary reports. Use blank or clearly fictitious values in the example. Prefer asking users to supply secrets again rather than keeping a reversible mapping to source values.

## 4. Personalization design

1. Load the generalized skill, declared schema, and supplied configuration as data; do not execute skill files.
2. Validate required fields, types, and supported formats. Report missing, invalid, or unexpected fields clearly.
3. Substitute values literally and only for declared placeholders. Never interpret values as template code, regular expressions, or shell fragments.
4. Write a new skill copy, preserving directory structure and file encoding where practical.
5. Confirm that declared placeholders have been resolved and report unused configuration entries.

Reject output paths that escape the chosen destination. Do not write secrets to logs or findings. Where the skill supports it, prefer environment-variable references for credentials over embedding values directly. Personalization does not execute the generated skill.

## 5. Round-trip and correctness expectations

For a skill with known configuration values, generalization should replace those values with declared placeholders; personalization with the matching configuration should restore them in the corresponding locations. Unrelated content, file names, and directory structure should remain intact.

Re-generalizing an unchanged personalized skill should yield a compatible placeholder/configuration template. This is a consistency check, not proof that every type of user-specific value was found. Where safely runnable fixtures exist, compare original and personalized skill outputs on the same inputs; only claim consistency for the tested cases.

## 6. Hermes plugin and module layout

```text
metaskill/
  generalize/
    __init__.py       # generalize_skill() entry point
    inventory.py      # bounded file discovery and type filtering
    detect.py         # deterministic candidate detectors
    classify.py       # optional contextual classification interface
    transform.py      # placeholder assignment and safe replacements
    config.py         # schema and example configuration generation
    report.py         # findings and residual-check output
  personalize/
    __init__.py       # personalize_skill() entry point
    config.py         # configuration loading and validation
    transform.py      # literal placeholder substitution
    validate.py       # unresolved placeholders and output checks
```

The repository already has the base plugin, `/metaskill` command, manifest, registration, and initial tool schema. Part 1 work described here extends that setup: implement the parser and transformation modules, expose generalize and personalize through the existing Hermes interface, and ensure Hermes can use the tool schema to select the appropriate operation. These are proposed module boundaries; implementation may adjust the file split while keeping the public operations and safety guarantees clear.

## 7. Tool surface

Expose both operations through the existing `/metaskill` command and `metaskill` tool. Extend the schema and handler so the requested operation and required skill/configuration paths are clear to Hermes. Example requests:

- `generalize ./skills/gcp-file-transfer`
- `personalize ./output/gcp-file-transfer-template --config ./config.json`

The handler should route to `generalize_skill()` or `personalize_skill()` with explicit input and output paths. The response should contain operation, status, artifact paths, finding/substitution counts, unresolved items, and warnings. Detailed findings belong in the generated report. **[OPEN]** Whether the initial Hermes schema should use one natural-language request or structured operation-specific parameters.

## 8. Testing

Use deterministic fixtures for skills with no sensitive values, known credentials, emails, phone numbers, local paths, organization settings, repeated values, ambiguous examples, malformed files, symlinks, and unsupported formats. Select 3–4 real Hermes skills for end-to-end validation as called for by the milestone plan.

- Generalization replaces expected values and creates matching schema fields.
- Personalization restores supplied values and rejects missing or invalid required values.
- Repeated scans of the same input produce the same deterministic findings and template.
- Known high-confidence source values do not remain in generalized output.
- The source tree and input configuration remain unchanged.
- Generated paths stay within the requested destination.
- Unrelated content and directory structure survive both transformations.
- Round-trip generalize/personalize/re-generalize produces a compatible template.
- Record whether the real-skill runs detect expected categories, preserve structure and formatting, and leave the generalized skills usable. Review false positives, false negatives, and behavior changes.

Use a fake classifier in automated tests. Real LLM calls should be separately marked and excluded from normal CI to avoid cost and nondeterministic results. Do not run arbitrary skill code as part of transformation tests. Report detection accuracy only when findings have been compared with a reviewed ground-truth set; otherwise document observed misses and false positives without claiming a measured rate.

## 9. Limitations and security considerations

- Detection can miss novel, encoded, split, or semantically represented secrets and information in unsupported or binary files.
- False positives can include public examples, sample domains, common paths, and benign identifiers.
- LLM processing may disclose source excerpts to a provider. Keep it optional, disclose its use, minimize submitted content, and follow applicable privacy requirements. Deterministic scanning should work without an LLM.
- Skill files are untrusted input and may contain prompt injection. Treat their contents and classifier responses as data, never as instructions to the tool.
- Static checks do not determine all runtime behavior and do not certify skill safety.
- Logs and reports must not expose detected credentials or personalized secret values.
- A successful round trip on fixtures does not prove equivalence for all inputs or complete PII removal.

## 10. Open questions

- Which file extensions and size/count limits are supported initially? (Section 3.1)
- Should user configuration use JSON or YAML? (Section 3.3)
- What review flow should the Hermes CLI provide for ambiguous or high-impact findings? (Section 3.2)
- Which optional LLM provider/classifier interface and privacy controls should be supported? (Sections 3.2 and 9)
- Should personalization produce a report file or only a tool response? (Section 2)
- Should generalize and personalize remain operations on the current `metaskill` tool, or become separate structured tools? (Section 7)
- Which 3–4 real Hermes skills will form the initial validation set, and how will expected sensitive values be annotated? (Section 8)
- Which prompts, tool declarations, metadata, and dependencies must the parser extract in the first version? (Sections 3.1 and 6)
