# Skill comparison: two action-item skills

A task file for comparing `document-to-action-items` and `meeting-action-items` (both ship with
Hermes) on the same work with `rank`. Both skills turn text into action items, and both say to mark a missing
owner as `unresolved`. They differ elsewhere: one keeps `may`, `should` and `must` apart, the
other separates decisions from proposals.

## Run it, inside Hermes

```
/metaskill rank document-to-action-items, meeting-action-items --tasks examples/skill-comparison/tasks.json --config examples/skill-comparison/eval-config.json
```

Start Hermes in the repo root. It makes about 12 to 16 short sessions in Docker (2 tasks, 2 runs
each, each skill and a baseline), so expect several minutes.

The config sets `"safety_tier": "deep"`, so the safety score also gets the LLM review of each
skill, which costs extra model calls (and the Cisco add-on's LLM analyzer joins in if it is
installed). Delete that line for the default static tier, which makes no extra calls.

## The tasks

- `meeting-notes`: `meeting-notes.txt` has one item with no owner, one idea that is not a decision
  and one explicit owner and date. The checks need the unowned item to name nobody, the idea not to
  be listed as a commitment, and the explicit owner and date to survive.
- `services-agreement`: `services-agreement.txt` has a `must`, a `should` and two `may` clauses. The
  checks need the deadline kept, the right to terminate not listed as an obligation, and the
  `should` not hardened into a `must`.

The checks are all deterministic. An LLM checklist is not used here: the judge sees only the agent's
reply, not the file it wrote, so a checklist about `actions.md` cannot be met and fails every run.

## What to expect

Without a skill the agent tends to give every item an owner and turn every clause into a duty,
so the baseline should fail the checks. The scores of the two skills will tell you which one's rules
the checks reward more. Read them as a comparison on these two tasks, not as a ranking of the skills.
Every skill still needs a Config Cleanliness score to get an overall, so the ranking itself
stays blank (see the open issues on evaluate UX).
