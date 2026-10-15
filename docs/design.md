# Skill Generalization and Personalization — Design

**Code lives in:** `metaskill/generalize/` and `metaskill/personalize/`.  
**Scope:** Part 1 of the project: generalizing a skill into a template and personalizing it from a configuration. Evaluation and merging are covered separately.

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

When this milestone is complete, a user can install the Metaskill plugin through Hermes, select an existing skill, and generalize it into a reusable template that keeps its intended functionality. It consists of:

- A reusable Hermes configuration template structure.
- Routing of generalization and personalization requests through the `/metaskill` command and the plugin tool schema.
- A skill project parser that detects PII and configuration in `SKILL.md`, scripts, and other relevant files.
- The generalization pipeline and the reverse personalization transformation.
- A tool schema that lets Hermes choose the right operation.
- Tests of generalization and personalization on real Hermes skills, including validation of functionality.
- Documentation of detection accuracy, edge cases, security risks, and limitations.

## 2. Inputs and outputs

### Generalize

**Input:** A Hermes skill project, typically a directory containing `SKILL.md` and supporting files, or a standalone `SKILL.md` when no project files are needed. An optional decisions file resolves ambiguous findings from an earlier run (see 3.2).

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

**Input:** The output directory produced by generalize (the generalized skill and its `config.schema.json`) and a user-supplied configuration file.

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

**Eligible special filenames:** Exact filenames and patterns such as `.env`, `.env.*`, `.npmrc`, `.pypirc`, `Dockerfile`, 
and `Makefile` are also eligible for inspection. Extensionless files are inspected when referenced by `SKILL.md` or when 
they contain a recognized script shebang.

**Limits (v1 defaults):** at most 500 files, 1 MiB per file, and 25 MiB total. A file over the size limit is skipped and listed in the findings report. A project over the file-count or total-size limit is rejected with a clear error rather than partially scanned.

Ignore version-control internals, caches, generated output, and binary files by default. List unsupported files in the findings report. Symlinks are never followed. They, and any path resolving outside the selected root, are skipped and listed in the findings report.

### 3.2 Detection and transformation

1. **Inventory:** Enumerate eligible files and record relative paths and file types.
2. **Deterministic detection:** Identify candidates using predefined rules and patterns for recognizable information such as email addresses, API keys, tokens, phone numbers, local/absolute file paths, repository URLs, environment variables, and common configuration assignments. The detector should return structured findings rather than immediately modifying the skill.
Environment-variable names and references, such as `os.getenv("OPENAI_API_KEY")`, are treated as existing configuration interfaces and are not replaced. The detector targets hard-coded values assigned to environment variables, such as `OPENAI_API_KEY="sk-..."`. Existing Hermes `required_environment_variables` and `metadata.hermes.config` declarations are preserved and reused where possible.
3. **Contextual classification:** Optionally use an LLM to classify ambiguous candidates that cannot be reliably determined through deterministic rules, such as distinguishing example values from real user-specific values or identifying organization-specific project names and settings. This layer is secondary to deterministic detection and is never required for generalization.

   - **Interface:** `classify.py` defines a `Classifier` protocol that takes a list of candidates and returns a label per candidate (`user_specific`, `example`, or `uncertain`). No provider is bundled in v1; a provider adapter implements the protocol.
   - **Off by default:** enabled only by an explicit opt-in flag, and the result reports that it was used.
   - **Privacy controls:** the classifier receives only a bounded context window around each candidate with the candidate value masked, never whole files, and never values the deterministic layer already classified as credentials.
   - **Untrusted data:** classifier responses are validated against the allowed labels; anything else is treated as `uncertain`.
4. **Review:** Non-interactive in v1. High-confidence findings are replaced automatically. Ambiguous findings are left unchanged and listed in `findings.json` with a stable finding ID, grouped by category and confidence. A user can re-run with an optional decisions file (finding ID to `approve` or `skip`) to resolve them; an interactive prompt is out of scope for v1.
5. **Placeholder assignment:** Replace approved user-specific values with standardized, reusable configuration parameters. Repeated occurrences of the same value should share a placeholder when they serve the same purpose.
6. **Configuration generation:** Create a field for each placeholder with a name, type, required flag, description, and validation constraints where known.
7. **Output validation:** Confirm each placeholder has a configuration definition and that approved values were replaced. Run residual detection and report remaining candidates. A clean report does not prove complete sanitization.

Example categories and placeholder names:

| Category | Examples | Placeholder |
|---|---|---|
| Credential | API token, password, private key | `{{config.api_token}}` |
| Personal contact | Personal email or phone | `{{config.contact_email}}` |
| Local environment | Home directory or machine-specific path | `{{config.workspace_path}}` |
| Organization | Internal host, team name, tenant ID | `{{config.organization_host}}` |
| Deployment setting | Region, project or account ID | `{{config.region}}` |

Not every URL, name, or path is treated as private by default: public documentation links, sample values, and portable relative paths may be reusable content. Replacements are exact and scoped to the detected occurrence rather than broad substitutions, so surrounding instructions and formatting are preserved.

### 3.3 Configuration template format

Generate a machine-readable schema and a human-editable example. Both are **JSON**, which the standard library parses safely with no extra dependency. YAML is not supported in v1. The example below shows `config.example.json`:

```json
{
  "api_token": "",
  "contact_email": "",
  "workspace_path": "",
  "organization_host": "",
  "region": ""
}
```

The schema marks sensitive fields so their values are excluded from logs and ordinary reports. The example contains only blank or clearly fictitious values. Secrets are never stored: users supply them again at personalization time, and no mapping back to the source values is kept.

## 4. Personalization design

1. Load the generalized skill, declared schema, and supplied configuration as data; do not execute skill files.
2. Validate required fields, types, and supported formats. Report missing, invalid, or unexpected fields clearly.
3. Substitute values literally and only for declared placeholders. Never interpret values as template code, regular expressions, or shell fragments.
4. Write a new skill copy, preserving directory structure and file encoding where practical.
5. Confirm that declared placeholders have been resolved and report unused configuration entries.

Output paths that escape the chosen destination are rejected. Secrets are never written to logs or reports. Where a skill supports it, credentials can be supplied as environment-variable references instead of literal values. Personalization does not execute the generated skill.

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

These modules plug into the base plugin (manifest, registration, `/metaskill` command, and tool schema), which exposes generalize and personalize to Hermes. The file split is a starting layout and may change, but the two public operations and the safety guarantees in this document stay fixed.

## 7. Tool surface

Both operations are exposed through the `/metaskill` command and the `metaskill` tool. The tool schema states the requested operation and the required skill and configuration paths so Hermes can pick the right one. Example requests:

- `generalize ./skills/gcp-file-transfer`
- `personalize ./output/gcp-file-transfer-template --config ./config.json`

Generalize and personalize remain **operations on the single `metaskill` tool**, selected by an `operation` enum parameter (`generalize` | `personalize`) alongside `skill_path`, `output_path`, and (for personalize) `config_path`. One tool keeps the schema small and the token cost low for the agent, and matches the existing manifest, which provides one tool. Evaluate and merge will be added as further operations. If the schema grows too large, splitting into separate tools is a later change that does not alter the handlers.

The handler routes to `generalize_skill()` or `personalize_skill()` with explicit input and output paths. The response contains the operation, status, artifact paths, finding and substitution counts, unresolved items, and warnings. Detailed findings are in the generated report.

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
- Real-skill runs record whether the expected categories were detected, whether structure and formatting were preserved, and whether the generalized skills remain usable, along with any false positives, false negatives, and behavior changes.

Automated tests use a fake classifier. Real LLM calls are marked separately and excluded from normal CI to avoid cost and nondeterministic results. Transformation tests never run arbitrary skill code. Detection accuracy is reported only when findings are compared against a reviewed ground-truth set; without one, observed misses and false positives are listed and no rate is claimed.

## 9. Limitations and security considerations

- Detection can miss novel, encoded, split, or semantically represented secrets and information in unsupported or binary files.
- False positives can include public examples, sample domains, common paths, and benign identifiers.
- LLM classification may send source excerpts to a provider. It is optional and off by default, its use is reported in the result, and only masked context windows are sent. Deterministic scanning works without it.
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
