# Real-skill trials (MET-17)

Generalize, then personalize, run on the four real Hermes skills that have reviewed ground truth (`tests/fixtures/ground-truth`, Hermes commit `78c7b9fda`). Each run goes through `tools.metaskill`, the handler Hermes calls.

Reproduce: `python tests/real_skill_trials.py` (add `--json` for raw numbers). `tests/test_real_skill_trials.py` pins these results.

## Method

1. Generalize the skill into a temporary directory.
2. Write a config that gives each placeholder the value it replaced.
3. Personalize the template with that config.
4. Compare the personalized skill with the original byte for byte, and parse the original, generalized and personalized `SKILL.md` with Hermes's own loader (`agent.skill_utils.parse_frontmatter`).
5. Score the findings against the ground truth (see `tests/fixtures/ground-truth/README.md`).

A byte-identical round trip means generalization changed no formatting and no behaviour: the only edits were placeholders, and they map back exactly.

## Results per skill

| Skill | Categories detected | Replaced | Left for review | False replacements | Unexpected reviews | Missed | Round trip | Hermes loads |
|---|---|---|---|---|---|---|---|---|
| `email/himalaya` | email 1, repository 1 | 1 | 1 | 1 | 1 | 0 | identical | same |
| `productivity/google-workspace` | email 4, repository 1, environment variable 2 (kept) | 1 | 4 | 1 | 4 | 0 | identical | same |
| `productivity/maps` | email 1 | 1 | 0 | 0 | 0 | 0 of 1 | identical | same |
| `social-media/xurl` | repository 5 | 0 | 5 | 0 | 5 | 0 | identical | same |
| **Total** | | 3 | 10 | 2 | 10 | 0 of 1 | 4 of 4 | 4 of 4 |

- **False positives.** Two false replacements, both placeholder addresses on `company.com` (`references/configuration.md:172` in himalaya, `references/gmail-search-syntax.md:53` in google-workspace). The 10 unexpected reviews are public upstream repository URLs (7) and stand-in addresses on `company.com` and `co.com` (3). A review never changes the skill: it waits for a decision.
- **False negatives.** None: the one expected value (maps) was replaced. These public skills hold almost no user-specific values, so this set cannot give a recall figure (see the ground-truth README).
- **Formatting and behaviour changes.** None. All four round trips are byte-identical, and Hermes reads the same name, description and body from the original, generalized and personalized `SKILL.md`. No placeholder landed in frontmatter.

## Gaps found and fixed

| Gap | Fix |
|---|---|
| The MET-12 pipeline (#34) and main's detection-only pipeline conflicted, and #34 dropped the add-on detectors (metaskill-laya). | Merged the two: the pipeline writes the template and runs registered extra detectors after the regex layer. Their findings are always left for review. The result again carries `detectors`, `note`, and per-action, per-category and per-detector counts. |
| Every review finding left in the output on purpose was also counted as an unexpected residual, so each run warned `residual_candidates` (14 across the four skills). | Residuals that match a review finding left in (same file, line, category and rule) are expected. Only new values count as unexpected (`pipeline.py`). |
| Public mail endpoints in provider setup guides (`imap.gmail.com`, `smtp.mail.me.com`) were flagged as configuration (4 reviews in himalaya). | Known provider IMAP/SMTP hosts are not configuration values (`detect.py`, `_PROVIDER_HOSTS`). |

Unexpected reviews went from 14 to 10, and the false residual warnings went from 14 to 0.

## Token cost

Estimated at about 4 characters per token.

| | himalaya | google-workspace | maps | xurl |
|---|---|---|---|---|
| `SKILL.md`, original → generalized | 1814 → 1814 | 3542 → 3542 | 1665 → 1665 | 4074 → 4074 |
| generalize tool result | 356 | 424 | 314 | 432 |
| personalize tool result | 100 | 100 | 100 | 100 |
| `findings.json` (only if the agent reads it) | 492 | 1616 | 230 | 1393 |

Fixed costs: the `metaskill` tool schema is about 173 tokens on every turn, and the bundled `metaskill:guide` skill is about 810 tokens when loaded. One generalize-then-personalize run adds about 410–530 tokens of tool results to the context. In these four skills no placeholder landed in `SKILL.md`, so the skill an agent loads is the same size after generalizing.

## Known limitations

- **`company.com` placeholders are replaced.** The detector treats `company.com` as a real domain on purpose (the MET-10 fixtures use it as their realistic domain), so documentation addresses such as `work@company.com` are replaced. Personalize restores them exactly, so the skill still works, but the template asks for a value it does not need. Changing this means moving the MET-10 fixtures to another domain.
- **Public repository URLs are left for review.** An upstream link (`homepage: https://github.com/pimalaya/himalaya`) looks the same as a user's own repository, so both are left for review rather than guessed at.
- **Add-on detection not trialled.** These runs used the regex layer only. metaskill-laya was not installed, so its false positives on these skills are not measured.
- **Not run by a live agent.** The trials call the tool handler and Hermes's skill loader directly. They do not run a Hermes chat session, so how the model reads the result, and the model's own token use, are not measured.
