# Detection ground truth (real Hermes skills)

Reviewed annotations of the sensitive values in four real Hermes skills, so the deterministic detector (`metaskill/generalize/detect.py`) can be measured instead of guessed. The skills themselves are the shared fixtures in `tests/fixtures/hermes-skills` (Hermes commit `78c7b9fda`, MIT); this folder only adds one `expected.json` per skill, following the convention in `docs/design.md` section 8.

| Skill | Shape | Expected values |
|---|---|---|
| `social-media/xurl` | `SKILL.md` only | 0 |
| `productivity/maps` | `SKILL.md` with a script | 1 |
| `email/himalaya` | `SKILL.md` with configuration references | 0 |
| `productivity/google-workspace` | scripts, environment variables, credential files | 0 |

## Format

- `files`: the sha256 of every file in the skill. Each file was reviewed in full, and a test fails if the fixture changes, so the annotation must be re-reviewed then.
- `expected`: values generalization should surface, by `file`, `line` (1-based), detector `category` and expected `action` (`replace` or `review`), with a note. Values are never stored.
- `retained`: notable values that were reviewed and must stay, such as placeholders, public URLs and portable paths, grouped by file with the reason.
- `review`: annotator, reviewer and status. All four are `provisional` until a second team member reviews them.

## Method

Annotations were made from the files, not from detector output: every Markdown file was read, and every string literal and comment in the Python scripts was extracted and checked. Each skill was also searched for emails, URLs, home and absolute paths, IP addresses, phone numbers and internal hostnames. The detector was run only after the annotations were written.

## Scoring

`python tests/ground_truth.py` prints the measurement; `tests/test_ground_truth.py` checks the annotations and the scorer.

- A finding matches an expected entry when file, line and category agree. Matching is one-to-one, so duplicate findings do not inflate detection.
- `replace` and `review` findings count as detections. Action agreement is reported separately.
- `keep` findings, such as environment-variable references, are configuration interfaces and are not scored.
- Because every file was reviewed, a `replace` finding with no expected entry is a false replacement. An unmatched `review` finding is reported as an unexpected review, which is noise rather than a change to the skill.

## Current result

On commit `8ba3b79`: 1 of 1 expected values detected with the expected action, 2 false replacements (placeholder addresses on `company.com`, which the detector does not treat as an example domain) and 14 unexpected reviews (public repository URLs, provider mail hosts and example addresses).

## Limitations

These are public, curated skills, so they contain almost no real user-specific values: one positive across four skills cannot support a detection rate. This set measures false replacements and review noise; measuring recall needs controlled variants with synthetic values seeded at known locations. The annotations are provisional until independently reviewed.
