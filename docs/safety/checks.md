# checks.py

Status: built and tested (`tests/safety/test_checks.py`, fixture skills in `tests/safety/fixtures/skills/`). Part of the default backends in `score()`.

## Purpose

Our own deterministic rules for the **careless** side of safety: a skill written in good faith whose instructions are still risky. No external scanner covers `destructive-unconfirmed` or `credential-logging`, so this is the part of the safety component that is new work (section 2.1 of the safety design).

Fully deterministic: no model, no network. The same skill always gives the same findings.

## Interface

```python
CHECKS_VERSION = "1"

class ChecksBackend:
    name = "own-checks"
    def available(self) -> bool: ...        # always True
    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult: ...

def delete_targets(skill: Skill) -> list[DeleteTarget]: ...
```

`delete_targets()` returns every unconfirmed delete with its target as data: the path as written, its location, whether the command follows symlinks, and the severity it was given. The local warnings in [local.md](local.md) use it to look the targets up on the user's machine. `scan()` uses the same parsing, so the two never disagree.

`tier` is ignored: the checks are the same at every tier. `CHECKS_VERSION` goes in the report header as `checks_version` and is bumped whenever a rule is added or changed, because a rule change can change scores.

## What is scanned

Decided: everything is scanned, so a dangerous command is not missed because of where it is written.

| Source | Scanned | Warning check |
|---|---|---|
| Fenced code blocks in `SKILL.md` and other `.md` files | Yes | No |
| Script files (`.sh`, `.bash`, `.zsh`, `.py`, `.js`, `.ts`, `.ps1`, `.psm1`, `.bat`, `.cmd`) | Yes, every line | No |
| Prose in markdown, including inline code (`` `rm -rf` ``) | Yes | Yes |
| Binary or very large files | No | — |

**Prose** is the ordinary sentences of a markdown file: explanations, warnings, steps written in words. Skills often warn about dangerous commands there, as in "never run `rm -rf` on the project root". Flagging those would make careful skills, which warn about risks, score lower than careless ones that do not. So matches in prose go through a warning check first.

### Warning check

Only for matches in prose. A command in a code block or a script is never skipped as a warning; it can only be cleared by the confirmation check below.

1. **Find the sentence** containing the match, splitting at `.`, `!`, `?` and line breaks.
2. **Look for a warning before the command** in that sentence: a negation (never, don't, do not, must not, should not, avoid, not) or a warning label (warning, caution, danger, important:, ⚠️). It must come before the command: in "Run `rm -rf build/`; never touch src/" the "never" is about something else.
3. **Warning blocks count too**, with or without a warning word: GitHub alerts (`> [!WARNING]`, `> [!CAUTION]`) and blockquotes starting with **Warning**, **Caution** or **Danger**.
4. **Exceptions** that look like warnings but are instructions are not skipped: "don't forget to", "do not skip", "never mind", "avoid ... by running".

A skipped match is not dropped silently. Each one is listed in `detail` with its location, e.g. `skipped as a warning: SKILL.md:12 (rm -rf)`, so a reader can check it. At the `deep` tier the LLM review re-checks them and can restore any that are real instructions ([review.md](review.md)).

| Text | Result | Correct? |
|---|---|---|
| "Never run `rm -rf` on the project root." | Skipped | Yes |
| `> [!WARNING]` block mentioning `git push --force` | Skipped | Yes |
| "Clean up by running `rm -rf ~/.cache/x`." | Finding | Yes |
| "Don't forget to run `rm -rf build/`." | Finding (exception) | Yes |
| "It is not uncommon to `rm -rf build/` here." | Skipped | No: a miss |

Keyword matching cannot follow grammar, so unusual sentences are misjudged. The skipped list in `detail`, the deep re-check and the golden dataset's cases are how those misses are caught and measured.

## Rules

Each rule has a stable ID (the `rule` field), a category, a severity and a fixed message.

Severities are decided. A severity answers: if the agent does this in an otherwise normal skill, how bad is it? It weighs whether the action can be undone, how much it can affect, and whether it harms only the user or others too. Since the safety score is set by the worst finding, one severity decides the whole score:

| Severity | Score if worst |
|---|---|
| CRITICAL | 0.0 |
| HIGH | 0.3 |
| MEDIUM | 0.6 |
| LOW, INFO | 0.9 |

### `destructive-unconfirmed`

| Rule | Matches | Severity |
|---|---|---|
| `recursive-delete` | `rm -r`, `rm -rf`, `rm -fr`, `shutil.rmtree`, `Remove-Item -Recurse`, `find ... -delete`; on Windows `rmdir /s`, `rd /s`, `del /s` | Set by the target, below |
| `force-push` | `git push --force`, `git push -f` | HIGH: can destroy other people's work on a shared branch |
| `history-rewrite` | `git reset --hard`, `git clean -fd` | HIGH: permanently discards uncommitted work or untracked files |
| `db-drop-database` | `DROP DATABASE` | CRITICAL: everything at once, like wiping a disk |
| `db-drop-table` | `DROP TABLE`, `TRUNCATE` | HIGH |
| `disk-write` | `dd ... of=/dev/`, `mkfs`, redirect to `/dev/sd*` | CRITICAL |

`git push --force-with-lease` is not matched: it refuses to overwrite work it has not seen.

#### Delete targets

The check cannot see the user's machine, so it cannot know what is actually at a path. It judges the target as written in the command:

| Target | Examples | Severity |
|---|---|---|
| System or home | `/`, `~`, `$HOME`, `/etc`, `/usr`, `/var`, `~/.ssh`, `~/.config`, `~/.gnupg`, `.git`, `..` | CRITICAL |
| Unguarded variable that may be empty | `rm -rf $DIR/` or `rm -rf "$DIR"/*`: an empty `DIR` turns it into `rm -rf /` | CRITICAL |
| Unknown | `data/`, `~/Documents/x`, `"$OUTPUT_DIR"`, `{{path}}`, `*`, `.`, any absolute path not listed here | HIGH |
| Rebuildable | `build/`, `dist/`, `out/`, `target/`, `node_modules/`, `__pycache__/`, `.cache/`, `.pytest_cache/`, `/tmp/...` | MEDIUM |

Rules for judging a target:

- **Rebuildable only when plain.** A relative path whose last part is in the rebuildable list, such as `build/`, `./dist` or `frontend/node_modules`, or a path under `/tmp/`. A trailing `/*` (the folder's contents) is allowed. Any `..`, other glob, variable or other absolute path makes it unknown. A path ending in `..`, such as `build/../../`, resolves to `..` and is CRITICAL, so it never passes as `build/`.
- **Variables set in the same script are followed once.** `DIR=build` followed by `rm -rf "$DIR"` is judged as `build`. Only one literal assignment in the same file; anything else is unknown.
- **Empty-variable guard.** `${DIR:?}` aborts when `DIR` is empty, so a guarded variable is judged as unknown, not CRITICAL.
- **Placeholders are unknown.** A generalized skill's `rm -rf {{OUTPUT_DIR}}` deletes whatever folder each user configures, so it is HIGH.
- **Several targets in one command:** the most severe one sets the severity.

#### Windows

Windows targets are converted to the forward-slash form above before they are judged, so the same rules and severities apply:

| Written as | Judged as |
|---|---|
| `C:\`, `C:\Windows\...`, `C:\Program Files\...`, `C:\ProgramData\...`, `C:\Users`, `C:\Users\<name>` | System or home: CRITICAL |
| `%USERPROFILE%`, `$env:USERPROFILE`, `${env:USERPROFILE}` | `$HOME`, so the home rules apply, including `.ssh` and `AppData` |
| `%TEMP%\x`, `$env:TEMP\x` | `/tmp/x`: rebuildable |
| Other `%VAR%`, `$env:VAR` followed by a separator | A variable that may be empty, CRITICAL, unless it is always set on Windows (`APPDATA`, `SystemRoot`, `ProgramFiles` and similar) |
| `.\dist`, `build\` | Relative, judged by name as on Unix |

Drive letters and Windows folder names are compared without case. Variables set in the same script are followed for `.bat`/`.cmd` (`set NAME=value`) and PowerShell (`$name = "value"`, `$env:NAME = "value"`). Windows confirmation steps also count: `set /p`, `choice`, `Read-Host`, and `-Confirm` or `-WhatIf` on the command. Arguments containing backslashes are split without shell escaping, so `C:\Windows` keeps its backslashes.

Tested on Linux with Windows-style scripts; not yet run on a Windows machine.

#### Symlinks

A folder that looks rebuildable can be a symlink to something important. Whether that matters depends on how the delete is written. Tested on Linux with `build` linked to a folder holding a file:

| Command | Follows the link? | Linked folder's contents |
|---|---|---|
| `rm -rf build` | No: removes only the link | Safe |
| `rm -rf build/` | Yes | Deleted |
| `rm -rf build/*` | Yes | Deleted |
| `shutil.rmtree("build")` | No: refuses symlinks | Safe |
| `find build/ -delete` | Yes | Deleted |

A symlink with a rebuildable name does not appear by itself: someone has to create it. A skill can do that in only two ways, and both are visible in the skill, so both are checked:

| Rule | Matches | Severity |
|---|---|---|
| `bundle-symlink` | A symlink inside the skill's own files that points outside the skill | CRITICAL if a delete in the skill targets it, otherwise MEDIUM (`excess-scope`: a skill has no reason to link outside itself) |
| `link-then-delete` | `ln -s` creating a link whose name a delete in the same skill targets with a form that follows links | CRITICAL |

When either applies and the delete follows links, the target is CRITICAL. A delete that does not follow links, such as `rm -rf build`, removes only the link, so the target is judged as written. The bundle is inspected with `lstat`, which reads the link itself without following it, and only inside the skill's folder.

What a static check cannot see is a symlink the user created on their own machine. That is covered by the local warnings ([local.md](local.md)) for paths that can be looked up, and later by the runner: task runs happen in a sandbox that only exposes the task's workspace, and comparing its files before and after a run shows what a delete actually removed.

**Confirmation check.** A match is only a finding if no confirmation step is found nearby. Nearby means:

- In markdown: the same code block, or the paragraph or list item directly before it.
- In scripts: the same line, or the 5 lines before it.

Confirmation means any of:

- Words: ask, confirm, confirmation, "are you sure", dry run, backup, back up.
- Flags: `--dry-run`, `-n` on `git clean`, `-i` or `--interactive` on `rm`.
- Code: `read -p`, `input(`, `confirm(`.

This is a heuristic (limitation in section 9 of the safety design). A confirmation written further away, or phrased differently, is missed and the command is reported.

### `credential-logging`

| Rule | Matches | Severity |
|---|---|---|
| `echo-secret` | `echo` or `printf` of a variable whose name contains KEY, TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL or AUTH | HIGH |
| `print-secret` | `print(`, `console.log(`, `logger.*(` of the same kinds of names | HIGH |
| `env-dump` | `env` or `printenv` with no arguments; `print(os.environ)`; `console.log(process.env)`. Code blocks and scripts only: in prose, `env` is usually a word or a config key | HIGH |
| `cat-credentials` | `cat` of `.env`, `~/.aws/credentials`, `~/.netrc`, `*.pem`, `id_rsa` | HIGH |
| `shell-trace` | `set -x` in a script that also reads a secret-named variable | MEDIUM |

A name counts as a secret when one of its parts is KEY, TOKEN, SECRET, PASSWORD, PASSWD, PASS, CREDENTIAL or AUTH, split at underscores and camelCase. A name ending in PATH, FILE, DIR, URL, NAME, METHOD, TYPE, ID and similar is about a secret, not the secret, so `TOKEN_PATH` and `auth_url` do not count.

Not covered here: secrets written into the skill itself (that is Configuration Cleanliness), and secrets sent to a third party (Cisco's `data_exfiltration`).

### `untrusted-source`

| Rule | Matches | Severity |
|---|---|---|
| `curl-pipe-shell` | `curl` or `wget` piped into `sh`, `bash`, `zsh`, `python`, also `bash <(curl ...)` and `sh -c "$(curl ...)"`. Not when the interpreter gets its own script (`python3 -c '...'`, `python3 parse.py`, `-m`): then the download is only parsed as data | HIGH |
| `insecure-download` | `curl -k`, `--insecure`, `wget --no-check-certificate`, `pip --trusted-host`, downloads over `http://` | MEDIUM |
| `unpinned-git-install` | `pip install git+...` or `npm install` from a git URL with no commit or tag | MEDIUM |
| `unpinned-install` | `pip install name` with no version, `npm install -g name` with no version | LOW |

`unpinned-install` is common in legitimate skills and caps each of them at 0.9. It stays: it is a real, minor supply-chain risk, and skills doing the same job are affected equally, so their ranking barely changes.

"Unknown source" in the safety design (an allowlist of trusted hosts) is not in version 1.

## Results on real skills

Run on 19 Oct 2026 against the 59 skills in a local `~/.hermes/skills`, static only:

| Worst finding | Skills |
|---|---|
| None | 43 |
| LOW (unpinned installs) | 10 |
| HIGH | 6 |

The first run had 18 skills at HIGH or above. Three rules were then narrowed: `curl | python3 -c` (parsing JSON, not running code), `` `env` `` in prose (a config key), and names like `TOKEN_PATH` (a path, not a secret). The six left:

- Three real `curl | bash` installers: `hermes-agent`, `notion`, `xurl`.
- Two deletes of a folder held in a variable that cannot be resolved: `rm -rf "$FRAME_DIR"` in `p5js`, `shutil.rmtree(segments_dir)` in `ascii-video`.
- `github`: `print(token)` in a git credential helper, which must print the token for git by design, and `echo "Using: $AUTH"`. Both are flagged correctly by the rule but are likely legitimate: candidates for the golden dataset's known false positives.

## Finding fields

| Field | Value |
|---|---|
| `dimension` | `safety` |
| `category` | From the tables above |
| `severity` | From the tables above |
| `source` | `"own-checks"` |
| `rule` | Rule ID |
| `quote` | The matched line, verbatim |
| `location` | `path:line`, relative to the skill |
| `message` | Fixed per rule, e.g. "Recursive delete with no confirmation step before it" |

One finding per matched line per rule.

## Tests

Fixture skills in `tests/safety/fixtures/skills/`: `clean`, `destructive`, `credentials`, `untrusted`, `warnings-only` and `confirmed`. These are meant to become the golden dataset's safety cases later. Other cases are built in temporary folders by the tests.

- `clean/`: no findings.
- One skill per rule that should fire.
- For each `destructive-unconfirmed` rule, a confirmed version that should not fire.
- Symlinks: each row of the symlink table, using a temporary folder; a bundle symlink pointing outside the skill (MEDIUM) and the same with a delete targeting it (CRITICAL); `ln -s ~ build` followed by `rm -rf build/` (CRITICAL) and by `rm -rf build` (no link-follow, judged as written); `find ... -delete` matched.
- `delete_targets()` returns the same targets and severities `scan()` reports.
- Delete targets: one case per row of the target table; `build/../../` judged unknown; `DIR=build` followed by `rm -rf "$DIR"` judged rebuildable; `rm -rf $DIR/` CRITICAL and `rm -rf "${DIR:?}"/` HIGH; a placeholder HIGH; two targets in one command take the worse.
- A skill that only warns about dangerous commands in prose: no findings, and each skipped match listed in `detail`.
- A dangerous command in prose with no warning: a finding.
- Each row of the warning-check table above, including the known miss, so a change to the word lists shows up in tests.
- A command in a code block next to a warning sentence: still a finding.
- A script with a confirmation 3 lines up (no finding) and 10 lines up (finding).
- Locations and quotes are exact.

## Open

- The score values behind each severity are placeholders (open question 3 in the safety design), so the effect of a severity on the score may change even though the severities are decided.
- The rebuildable and system target lists are a first version.
- The warning and exception word lists are a first version, to be tuned against the golden dataset.
- Trusted-host allowlist for `untrusted-source`.
