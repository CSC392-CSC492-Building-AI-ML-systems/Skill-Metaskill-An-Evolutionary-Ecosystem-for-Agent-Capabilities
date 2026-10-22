# Ticket-summary pilot

Status: draft, synthetic, not independently reviewed. No live Hermes runs, lift measurements, transformation runs, or scored skill rankings exist yet.

This pilot supports Quality and Configuration Cleanliness without requiring services or credentials. It is intentionally easy; zero lift is a valid possible outcome. It is not a benchmark of real-world usefulness.

## Contents and boundaries

- `skills/reference/`: configured instruction-only skill. This initial fixture omits optional frontmatter to avoid adding PyYAML to the current offline CI dependency set.
- `hardcoded`, `undocumented`, `unused`: targeted configuration variants.
- `wrong-totals`: a Quality defect; expected behavioural degradation is a hypothesis for live runs, not a guaranteed LLM response.
- `inputs/`, `expected/`: eight hand-authored inputs and Alpha answers; expected-beta contains derived Beta answers, not independent labels.
- `profiles/`: fictitious organization settings for alternate-user checks.
- `cases.json`, `configuration_expectations.json`: draft gold inputs, not runtime report objects.
- `contract.md`: public requirements delivered identically to both experimental variants.

No scripts are bundled with the skill. The verifier is evaluator-owned outside the skill directory and must not be exposed as an answer-generation tool.

## First review

An independent teammate checks the task contract, manually recomputes the totals, and verifies each annotated configuration defect. Record reviewer, date, and disagreements before marking this approved gold. Do not copy generated oracle results back into the answer files as independent labels.

## Paired-run procedure

For each case, prepare a fresh workspace with only the selected input and profile (named config.json), plus the full public contract. Ask the agent to summarize input.json using config.json and return only the contract JSON. Use the same prompt, tools, model, limits, and input state for baseline and with-skill runs. Load only the candidate SKILL.md in the with-skill condition. Do not give the baseline a broken configuration, or hide task requirements from it.

Repeat three times per condition when the runner is ready. Keep candidate outputs as JSON artifacts and record filesystem changes independently. The semantic verifier checks JSON content; it cannot establish that an agent avoided network use or source modification.

The manifest explicitly schedules all eight inputs for each of five skill variants and both profiles: 80 logical cases. Each case has a unique case_id; task_id identifies the shared input/profile problem independently of candidate skill. Load the selected profile rather than the candidate bundle's example config.json. Beta expectations change only the organization label; invalid-input answers stay identical. All candidates retain the correct public expected outputs, including wrong-totals: its defective instructions never redefine success.

Each logical case is planned for three baseline and three with-skill runs (480 sessions if executed naively). This is a draft matrix, not a request to spend that budget now. Start with reference and hardcoded on normal input under both profiles for a 24-session smoke experiment; do not publish that subset as full-matrix evaluation. Baseline reuse across candidates requires the runner owner's agreement.

## Transformation handoff

The reference is configured, not a claimed output of generalize. The transformation team can use the hardcoded organization occurrence as a non-secret organization candidate; its classification may require a reviewed decisions file. Expected transformation: parameterize the approved occurrence, produce declared configuration, personalize with Beta, then preserve report semantics. Do not assume organization detection is already implemented or automatic.

## Integration status

Current Check kinds cannot express semantic JSON equality. This pilot verifier is a separate trusted helper, not a new Check kind. Do not replace it with file-existence or regex checks that would accept wrong totals. A future runner adapter should call the helper on captured output from trusted evaluator code; any new Check/model fields require separate agreement.

Run offline verification from repository root with `python -m pytest`. That verifies fixture/oracle consistency and verifier rejection behaviour, not live skill performance.


## Known fixture mutation

The wrong-totals skill consistently instructs inclusion of cancelled tickets, including cancelled-only inputs. This deliberately conflicts with the public task requirement to exclude them. It tests adherence to an incorrect instruction, without adding an internal contradiction. Agents may follow the public contract instead; no guaranteed score ordering is asserted.
