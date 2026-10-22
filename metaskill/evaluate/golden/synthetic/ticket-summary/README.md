# Ticket-summary pilot

Status: draft, synthetic, not independently reviewed. No live Hermes runs, lift measurements, transformation runs, or scored skill rankings exist yet.

This pilot supports Quality and Configuration Cleanliness without requiring services or credentials. It is intentionally easy; zero lift is a valid possible outcome. It is not a benchmark of real-world usefulness.

## Contents and boundaries

- `skills/reference/`: configured instruction-only skill. This initial fixture omits optional frontmatter to avoid adding PyYAML to the current offline CI dependency set.
- `hardcoded`, `undocumented`, `unused`: targeted configuration variants.
- `wrong-totals`: a Quality defect; expected behavioural degradation is a hypothesis for live runs, not a guaranteed LLM response.
- `inputs/`, `expected/`: eight hand-authored inputs and answers.
- `profiles/`: fictitious organization settings for alternate-user checks.
- `cases.json`, `configuration_expectations.json`: draft gold inputs, not runtime report objects.
- `contract.md`: public requirements delivered identically to both experimental variants.

No scripts are bundled with the skill. The verifier is evaluator-owned outside the skill directory and must not be exposed as an answer-generation tool.

## First review

An independent teammate checks the task contract, manually recomputes the totals, and verifies each annotated configuration defect. Record reviewer, date, and disagreements before marking this approved gold. Do not copy generated oracle results back into the answer files as independent labels.

## Paired-run procedure

For each case, prepare a fresh workspace with only the selected input and profile (named config.json), plus the full public contract. Ask the agent to summarize input.json using config.json and return only the contract JSON. Use the same prompt, tools, model, limits, and input state for baseline and with-skill runs. Load only the candidate SKILL.md in the with-skill condition. Do not give the baseline a broken configuration, or hide task requirements from it.

Repeat three times per condition when the runner is ready. Keep candidate outputs as JSON artifacts and record filesystem changes independently. The semantic verifier checks JSON content; it cannot establish that an agent avoided network use or source modification.

For profile Beta, substitute Example Beta into the expected organization field only; totals stay the same. Compare profiles against reference and hardcoded variants. This cross-profile case exposes a task-family-specific configuration defect.

## Transformation handoff

The reference is configured, not a claimed output of generalize. The transformation team can use the hardcoded organization occurrence as a non-secret organization candidate; its classification may require a reviewed decisions file. Expected transformation: parameterize the approved occurrence, produce declared configuration, personalize with Beta, then preserve report semantics. Do not assume organization detection is already implemented or automatic.

## Integration status

Current Check kinds cannot express semantic JSON equality. This pilot verifier is a separate trusted helper, not a new Check kind. Do not replace it with file-existence or regex checks that would accept wrong totals. A future runner adapter should call the helper on captured output from trusted evaluator code; any new Check/model fields require separate agreement.

Run offline verification from repository root with `python -m pytest`. That verifies fixture/oracle consistency and verifier rejection behaviour, not live skill performance.

