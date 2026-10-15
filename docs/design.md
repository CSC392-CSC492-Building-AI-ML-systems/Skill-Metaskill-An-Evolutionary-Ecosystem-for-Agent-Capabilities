# Skill Generalization and Personalization — Design

**Code lives in:** `metaskill/generalize/` and `metaskill/personalize/` (planned; these modules are not yet present).  
**Scope:** Part 1: Skill Generalization. The base Hermes plugin and `/metaskill` command are already set up.

## 1. Purpose

Given an existing Hermes skill, analyze its structure and contents—including `SKILL.md` and relevant supporting files—then identify user-specific information, credentials, PII, and organization-specific configuration. Separate those values from reusable skill logic, replace them with reusable configuration parameters or placeholders, and generate a reusable skill template. Given that generalized skill and a new user's configuration, perform the reverse transformation to produce a personalized skill while preserving the original behavior.
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

**Output:** A separate directory containing the personalized skill and a concise result report, `personalize-report.json`, listing the placeholders resolved, unused configuration entries, and warnings, without any configuration values. The same summary is returned in the tool response. The input skill and configuration remain unchanged.

## 3. Generalization design

### 3.1 File inspection

Resolve the input path and define a bounded scan root. Parse the skill project and inspect `SKILL.md` plus relevant supporting files, including scripts. Do not execute files.

**Extracted in v1:**

- Frontmatter metadata from `SKILL.md` (name, description, version, and any other keys).
- Prompt and instruction text from `SKILL.md`.
- Tool declarations, such as tool names listed in frontmatter or referenced in the body.
- Scripts and files referenced from `SKILL.md`.
- Environment variables read or referenced by the skill or its scripts.
- Dependencies from manifests (`requirements.txt`, `pyproject.toml`, `package.json`) and import statements in scripts.

**Eligible files (allowlist):** `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.py`, `.sh`, `.js`, `.ts`.

**Limits (defaults, configurable):** at most 500 files, 1 MiB per file, and 25 MiB total. A file over the size limit is skipped and listed in the findings report. A project over the file-count or total-size limit is rejected with a clear error rather than partially scanned.

Ignore version-control internals, caches, generated output, and binary files by default. List unsupported files in the findings report. Reject or report symlinks and paths resolving outside the selected root.

### 3.2 Detection and transformation

1. **Inventory:** Enumerate eligible files and record relative paths and file types.
2. **Deterministic detection:** Identify candidates using predefined rules and patterns for recognizable information such as email addresses, API keys, tokens, phone numbers, local/absolute file paths, repository URLs, environment variables, and common configuration assignments. The detector should return structured findings rather than immediately modifying the skill.
3. **Contextual classification:** Optionally use an LLM to classify ambiguous candidates that cannot be reliably determined through deterministic rules, such as distinguishing example values from real user-specific values or identifying organization-specific project names and settings. This layer is secondary to deterministic detection and is never required for generalization.

   - **Interface:** `classify.py` defines a `Classifier` protocol that takes a list of candidates and returns a label per candidate (`user_specific`, `example`, or `uncertain`). No provider is bundled in v1; a provider adapter implements the protocol.
   - **Off by default:** enabled only by an explicit opt-in flag, and the result reports that it was used.
   - **Privacy controls:** the classifier receives only a bounded context window around each candidate with the candidate value masked, never whole files, and never values the deterministic layer already classified as credentials.
   - **Untrusted data:** classifier responses are validated against the allowed labels; anything else is treated as `uncertain`.
4. **Review:** Non-interactive in v1. High-confidence findings are replaced automatically. Ambiguous findings are left unchanged and listed in `findings.json` with a stable finding ID, grouped by category and confidence. A user can re-run with an optional decisions file (finding ID to `approve` or `skip`) to resolve them; an interactive prompt is out of scope for v1.
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

Generate a machine-readable schema and a human-editable example. Both are **JSON**: it needs no extra dependency, parses safely with the standard library, and the schema is itself JSON. YAML is not supported in v1.

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

Generalize and personalize remain **operations on the single `metaskill` tool**, selected by an `operation` enum parameter (`generalize` | `personalize`) alongside `skill_path`, `output_path`, and (for personalize) `config_path`. One tool keeps the schema small and the token cost low for the agent, and matches the existing manifest, which provides one tool. Evaluate and merge will be added as further operations. If the schema grows too large, splitting into separate tools is a later change that does not alter the handlers.

The handler should route to `generalize_skill()` or `personalize_skill()` with explicit input and output paths. The response should contain operation, status, artifact paths, finding/substitution counts, unresolved items, and warnings. Detailed findings belong in the generated report.

The agent only ever sees counts, categories, file paths, and artifact paths, never the detected values. Detection and replacement run inside the tool handler, so sensitive values are not placed in the agent's context.

## 8. Testing

Use deterministic fixtures for skills with no sensitive values, known credentials, emails, phone numbers, local paths, organization settings, repeated values, ambiguous examples, malformed files, symlinks, and unsupported formats. Select 3–4 real Hermes skills for end-to-end validation as called for by the milestone plan (selection and annotation are tracked in MET-8). Ground truth for each real skill is an `expected.json` recording the file, line, and category of each expected sensitive value, not the values themselves, so accuracy can be measured without storing secrets in the repository.

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

## 10. Decisions

Questions raised during drafting and how they were resolved. Each is reflected in the section noted.

| Question | Decision | Section |
|---|---|---|
| File extensions and size/count limits | Allowlist and limits as listed; over-limit files skipped, over-limit projects rejected | 3.1 |
| JSON or YAML for configuration | JSON | 3.3 |
| Review flow for ambiguous or high-impact findings | Non-interactive; ambiguous findings reported and resolved by an optional decisions file | 3.2 |
| LLM provider, classifier interface, privacy controls | `Classifier` protocol, off by default, masked bounded context, no bundled provider | 3.2, 9 |
| Personalization report file or tool response only | Both: `personalize-report.json` and the tool response | 2 |
| One tool or separate tools | One `metaskill` tool with an `operation` enum | 7 |
| Real-skill validation set and annotation | Selected under MET-8; `expected.json` with file, line, and category | 8 |
| What the parser extracts in v1 | Frontmatter, prompts, tool declarations, referenced scripts, env vars, dependencies | 3.1 |
