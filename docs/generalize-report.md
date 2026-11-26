# Generalize: Detection Accuracy, Edge Cases, and Limits

How well `generalize` separates a skill's reusable logic from its author's personal and organization configuration, measured on the code in `main` as of 16 Nov 2026. The design is in [design.md](design.md). This document reports what the code does today, including where it differs from the design.

## 1. Current state

`generalize` only detects so far. It inventories the skill, runs the regex layer (`metaskill/generalize/detect.py`) and any add-on detector such as [metaskill-laya](../plugins/metaskill-laya/README.md), and returns counts. It does not yet write placeholders, `config.schema.json`, `findings.json`, or a generalized skill (MET-12), and `personalize` is not implemented. Its status is `partial`, so the tool returns `success: false`.

So the only thing that can be measured today is **detection**: whether a value is found and which action it gets.

| Action | Meaning | Assigned to |
|---|---|---|
| `replace` | Will be replaced automatically once MET-12 lands | Credentials, and any finding with confidence 0.9 or higher |
| `review` | Left unchanged and listed for a decision | Lower-confidence findings, and everything from an add-on model |
| `keep` | Already a configuration interface | Environment variable references, such as `os.getenv("API_KEY")` |

The optional LLM classifier (`classify.py`) is an interface with no provider and is not called by the pipeline. Everything below is the regex layer alone, except where Laya is named.

## 2. Detection accuracy

[design.md](design.md) section 8 says to report an accuracy rate only against a reviewed ground truth. Two exist, both small and hand-written. The real-skill ground truth (`expected.json` per skill, MET-8) is not in the repository yet, so no accuracy rate is claimed for real skills.

### 2.1 Hand-written fixtures (regression check)

`tests/skill_fixtures/` has 11 small skills, each listing the exact findings expected: no sensitive values, credentials, emails, phone numbers, local paths, organization settings, repeated values, ambiguous and example values, malformed files, symlinks, and unsupported files. `tests/test_generalize_fixtures.py` checks that every expected finding is reported with the right line, category, rule, confidence, and action, and nothing else; that no secret appears in any finding, report, or `repr`; that IDs are stable across runs; and that the files are unchanged.

All pass (449 passed, 7 skipped across `tests/test_generalize_*.py` and `tests/laya_addon`). This shows the detector matches the authors' expectations on those cases. It is not an accuracy rate, because the expectations were written alongside the rules.

### 2.2 Labeled corpus (`benchmarks/pii_compare`)

10 skill-like files with 34 fake personal values that should be flagged and 12 decoys that should not. Regex figures are from a run on 16 Nov 2026. Laya figures are from [its README](../plugins/metaskill-laya/README.md) and were not rerun here.

| | Regex only | Regex + Laya |
|---|---|---|
| Personal values found (34) | 21 (62%) | 34 (100%) |
| Structured: emails, phones, keys, IPs, paths, hosts, repos (20) | 20 (100%) | 20 (100%) |
| Free-form: names, addresses, birth dates, card and SSN numbers (14) | 1 (7%) | 14 (100%) |
| False positives | 1 | 2 |
| Time, all 10 files | under 0.1 s | 34 s CPU, 2.4 s GPU |

The regex layer's false positive is `us-east-1` assigned to a `region` key, a public value. Its 13 misses are all free-form personal data (names such as "Priya Nair", street addresses, birth dates, a card number, an SSN), which no pattern in the regex layer targets.

The corpus was written by people who know both detectors, so treat these figures as a smoke test. They show where each detector is strong, not how either performs on real skills.

### 2.3 Real Hermes skills (observed, no ground truth)

The regex layer was run over the 58 public skills bundled with Hermes (`tests/fixtures/hermes-skills`, 309 files scanned, 8 skipped). Public skills should hold little personal data, so this mainly shows false positives. Misses cannot be counted without a reviewed ground truth.

| Finding | Count | Action | Observation |
|---|---|---|---|
| Credentials | 0 | | Fillers such as `sk-xxxxxxxx` were correctly ignored |
| Emails | 3 | replace | All false positives for this purpose: `work@company.com` and `accounting@company.com` are documentation examples; `hermes@agent.ai` is the skill's own contact in a User-Agent, which is reusable |
| Emails (role or sample) | 4 | review | `boss@company.com`, `alice@co.com`, `bob@co.com`, `hello@cocoon-ai.com`: examples or a published mailbox |
| Repositories | 30 | review | Public upstream links; correctly not auto-replaced |
| Config assignments | 6 | review | All reusable: public mail servers (`imap.gmail.com`), `X-Team-Id: "engineering"` in an example, `[user's language]` |
| Environment variable references | 100 | keep | Correctly kept as configuration interfaces |
| Phones, home paths, internal hosts | 0 | | |

On these skills, nothing reusable would be replaced except the 3 emails, and all 40 review findings are reusable content. The review queue is noisy: a user generalizing one of these skills would have to skip every item.

## 3. Edge cases

Each case was run against `detect_file` on 16 Nov 2026.

| Case | Result | Why |
|---|---|---|
| Base64-, hex-, or reversed-encoded token | Missed | Patterns match the raw shape only; nothing decodes |
| Token in JSON with a `sk-...` escape | Missed | Text is matched as written, not as parsed |
| Token split across `"ghp_..." + "..."` | Partly found | The first fragment is caught as a `secret_assignment`; the second fragment stays in the file |
| Token split across shell variables, `A=...; B=...; "$A$B"` | Missed | Neither fragment has a recognizable shape or a secret-like name |
| Secret in prose, "the wifi password is hunter2horse99" | Missed | Assignments only count in code, config files, frontmatter, and fenced blocks |
| `api_key = ...` in Markdown prose, outside a code block | Missed | Same reason; this avoids flagging sentences such as "Owner: see below" |
| URL with password, `postgres://admin:p%40ss...@host` | Found | Only the password is a credential; the username `admin` stays |
| `AKIAIOSFODNN7EXAMPLE`, `sk-your-key-here`, `sk-xxxx` | Ignored, correctly | Example and filler checks |
| A real-shaped key in an "Example:" block | Found and replaced | Shape wins over context; a realistic example is treated as real |
| `jane.doe@example.com`, `(702) 555-0123` | Ignored, correctly | Reserved example domains and 555 numbers |
| `priya@acme-corp.com`, `(416) 829-4471` | Found and replaced | No way to tell a made-up example on a real-looking domain from a real one |
| Team, project, or channel names in prose ("Team Falcon", `#payments-oncall`) | Missed | No shape to match; left to the classifier, which is not wired in |
| `~/Documents/acme-notes` | Ignored | `~` is portable; only absolute home paths such as `/Users/jsmith/...` are flagged |
| Binary file (contains a NUL byte) | Skipped, listed as `binary` | Never decoded or scanned |
| Non-UTF-8 text file | Skipped, listed as `not_utf8` | Never scanned |
| `.html`, `.mjs`, `.csv`, `.go`, `LICENSE` and other unlisted types | Skipped, listed as `unsupported` | Not on the allowlist. The Hermes skills include two HTML templates and a `.mjs` file that were not scanned |
| File over 1 MiB | Skipped, listed as `too_large` | Size limit |
| Symlink | Skipped, listed as `symlink` | Never followed |

Skipped files are counted in the result's warnings, but nothing in them is detected. Once templates are written, a skipped file will be copied as-is, so any personal value in it will be shared.

**Examples versus real values.** The regex layer handles common filler (`your-`, `example`, `xxxx`, `<...>`, `{{...}}`, `my-project`), reserved domains and numbers, stock names (`alice`, `john.doe`), role mailboxes (`support@`), and public sample AWS account IDs. Anything outside those lists is judged by shape alone. A realistic example is treated as real (replaced, a false positive) and a real value with a sample-looking name is treated as an example (kept, a miss). The classifier was meant to settle these cases but is not yet called.

## 4. Security risks

### 4.1 LLM provider disclosure

No provider is bundled and the pipeline does not call the classifier, so nothing is sent to an LLM today. When a provider is added, the current interface:

- Never sends credentials: credential findings are not candidates, and their spans are replaced with `[REDACTED:credential]` in every context window.
- Sends only a few lines around each candidate (2 by default), never whole files.
- **Sends the candidate value in clear.** [design.md](design.md) section 3.2 says the value is masked. The code sends emails, phones, paths, hosts, and repository URLs as they appear.
- **Does not redact other non-credential findings in the context window**, so a nearby email or phone is also sent.
- **Cannot redact what was not detected.** A missed secret, such as an encoded or split token, goes to the provider inside the context lines.

The Laya add-on runs locally. Its only network use is the first model download from the Hugging Face Hub, pinned to a revision and checked against a SHA-256. `METASKILL_LAYA_MODEL` skips both checks.

### 4.2 Prompt injection from skill files

Skill files are untrusted. The current defenses:

- Detection runs inside the tool handler. The agent receives counts, categories, and paths, never file content or detected values.
- Classifier responses are checked: unknown IDs and labels are dropped and become `uncertain`, `replace` is only honored for a known label, and suggested config keys must match `[a-z][a-z0-9_.]{0,63}`.
- Add-on hits are checked: out-of-range spans, spans overlapping a regex finding, and duplicates are dropped.
- Exceptions from the pipeline and add-ons are reported by type only, since messages may quote file content.

What remains: once a classifier is added, a skill file can tell the model to label its own real values `reusable`. Validation limits the model to allowed labels, but not to the right ones, so injection can cause under-redaction. The classifier should only ever be able to escalate a finding to `replace` or `review`, never downgrade a `replace`.

### 4.3 Secrets in logs and reports

- Credential values are masked in every `Finding` (`ghp_********`), and `SourceFile` and `SkillProject` hide their contents from `repr`. Tests check that no fixture secret appears in findings, reports, or `repr`.
- Nothing in `metaskill/generalize` logs.
- **Non-credential values are stored in clear in `Finding.value`.** [design.md](design.md) section 2 says findings must not copy sensitive values. When `findings.json` is written (MET-12), it will contain emails, phones, and home paths unless they are masked first.
- A URL password is masked, but the username in the same URL is not.
- A Laya model-load error includes up to 200 characters of the exception, which can name a local file path, though never skill content.

### 4.4 No proof of full sanitization

A clean result means only that no rule matched. The edge cases in section 3 show realistic ways a secret survives: encoding, splitting, prose, unsupported file types, binary files. The residual scan after replacement (design.md section 3.2 step 7) is not built yet, and when it is, it will rerun the same rules, so it cannot find what they miss. Users should review a generalized skill before sharing it.

## 5. Known limitations

- Detection only; no template, config, or personalized skill is produced yet (MET-12, personalize).
- No reviewed ground truth on real skills, so no real-world accuracy rate.
- The regex layer finds 7% of free-form personal data (names, addresses, birth dates, ID numbers) on the labeled corpus. Laya covers this, but it is optional, about 1 GB, and slow on CPU.
- Organization-specific plain strings (team, project, channel, and tenant names without a known host suffix) are not detected.
- Shape-based example checks misjudge realistic examples and sample-looking real values.
- The review queue on public skills is entirely reusable content (40 of 40 on the Hermes set).
- Files outside the extension allowlist, binary files, non-UTF-8 files, and files over 1 MiB are not scanned.
- The classifier interface sends unmasked candidate values, unlike the design.
- Laya's category labels are unreliable, so its findings are all reported as `personal_data` and always need review.

## 6. Future improvements

- Annotate 3 to 4 real skills with `expected.json` (MET-8) and report recall and precision per category against them.
- Mask non-credential values in `Finding.value` before `findings.json` is written, keeping only category, rule, and location, as the design requires.
- Mask the candidate value, and redact all detected spans, in classifier requests.
- Let the classifier only escalate findings, so injection cannot cause a `replace` finding to be kept.
- Decode common encodings (base64, hex, JSON and URL escapes) before matching, and join adjacent string literals.
- Scan `.html`, `.mjs`, `.cjs`, `.xml`, and `.csv` as text, or warn more prominently when a skipped file would be copied into a template.
- Reduce review noise: treat repository links to a skill's own upstream, public mail servers, and `company.com` style examples as reusable.
- Grow `benchmarks/pii_compare` with cases taken from real skills, written by someone who did not write the rules.
