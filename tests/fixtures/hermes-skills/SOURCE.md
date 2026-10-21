# Hermes skills (test fixtures)

The 58 skills bundled with Hermes Agent, copied unchanged as real-skill test data shared by every component (generalize, personalize, evaluate). Load them with `tests/hermes_skills.py`.

- **Source:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), folder `skills/`, commit `78c7b9fda` (4 Sep 2026)
- **Licence:** MIT, copyright (c) 2025 Nous Research. See [LICENSE](LICENSE) in this folder. Each skill also declares `license: MIT` in its frontmatter.
- **Copied with:** `git -C <hermes-agent checkout> archive 78c7b9fda skills | tar -x --strip-components=1`. Two repository files that are not skills, `AGENTS.md` (a guide for skill authors) and `index-cache/` (cached indexes of other skill hubs), were then removed.

These are third-party files used as test data. Do not edit them; to update, copy a newer commit the same way, then refresh any snapshot that depends on them.

Expected results live with the component that tests them, not here. For example, the own safety checks' snapshot is `tests/safety/hermes_expected.json`, checked by `tests/safety/test_hermes_skills.py` and refreshed with `tests/safety/update_hermes_snapshot.py` when a rule change is meant to change findings on real skills.
