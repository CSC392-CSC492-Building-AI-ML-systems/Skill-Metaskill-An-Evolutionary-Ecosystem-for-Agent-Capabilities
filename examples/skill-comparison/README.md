# Skill comparison: two action-item skills

A task file for comparing `document-to-action-items` and `meeting-action-items` (both ship with
Hermes) on the same work with `rank`. Both skills turn text into action items, and both say to mark a missing
owner as `unresolved`. They differ elsewhere: one keeps `may`, `should` and `must` apart, the
other separates decisions from proposals.

## Run it, inside Hermes

```
/metaskill rank document-to-action-items, meeting-action-items --tasks examples/skill-comparison/tasks.json --config examples/skill-comparison/eval-config.json
```

Start Hermes in the repo root. It makes 16 short sessions in Docker (2 skills × 2 tasks × 2 runs,
each with the skill and without it), so expect several minutes.

The config sets `"safety_tier": "deep"`, so the safety score also gets the LLM review of each
skill, which costs extra model calls (and the Cisco add-on's LLM analyzer joins in if it is
installed). Set it to `"static"`, the default tier, to make no extra calls.

## The tasks

- `meeting-notes`: `meeting-notes.txt` has one item with no owner, one idea that is not a decision
  and one explicit owner and date. The checks need the unowned item to name nobody, the idea not to
  be listed as a commitment, and the explicit owner and date to survive.
- `services-agreement`: `services-agreement.txt` has a `must`, a `should` and two `may` clauses. The
  checks need the deadline kept, the right to terminate not listed as an obligation, and the
  `should` not hardened into a `must`.

The checks match text, so they are approximate. A proposal can be marked on its line (`consider`,
`no decision`) or by the heading just above it ("Proposals"; not "Proposed tasks", which lists things
to do). A right to terminate can be shown by "may", "if" or a "Rights" heading, on any line about
it, as long as no other line orders it. The report's "should" needs to appear on one line about the
report, with none saying it is required. The missing owner of the pricing page has to be in a
table's owner column, after an `owner:` label, or said in words such as "unassigned". A layout the
checks do not expect, such as a proposal two headings deep, can fail although it is correct.

The checks are all deterministic. An LLM checklist is not used here: the judge sees only the agent's
reply, not the file it wrote, so a checklist about `actions.md` cannot be met and fails every run.

## What to expect

On one run with `gpt-5.4-nano` (2 runs of each task, with and without each skill), every output kept
the first five rules, with or without a skill. Only the report's "should" told them apart:
`document-to-action-items` kept it in both runs, while `meeting-action-items` and every run without
a skill turned it into a duty. So `meeting-notes` does not separate the skills on that model. A
stronger or weaker model may differ.

Read the scores as a comparison on these two tasks, not as a ranking of the skills. Every skill still
needs a Config Cleanliness score to get an overall, so the ranking itself stays blank (see the open
issues on evaluate UX).
