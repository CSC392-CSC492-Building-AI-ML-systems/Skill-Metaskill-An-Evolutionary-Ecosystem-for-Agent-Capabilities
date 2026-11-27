# checks.py

Status: built and tested (`tests/safety/test_checks.py`, fixture skills in `tests/safety/fixtures/skills/`). Part of the default backends in `score()`.

## Purpose

Our own deterministic rules for the **careless** side of safety: a skill written in good faith whose instructions are still risky. No external scanner covers `destructive-unconfirmed` or `credential-logging`, so this is the part of the safety component that is new work (section 2.1 of the safety design).

Fully deterministic: no model, no network. The same skill always gives the same findings.

## Interface

```python
CHECKS_VERSION = "8"

class ChecksBackend:
    name = "own-checks"
    def available(self) -> bool: ...        # always True
    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult: ...

def delete_targets(skill: Skill) -> list[DeleteTarget]: ...
def skipped_warnings(skill: Skill) -> list[SkippedWarning]: ...
```

`skipped_warnings(skill)` returns each prose match skipped as a warning, with its sentence, paragraph and the finding it would become, for the deep review's re-check ([review.md](review.md)). Matches that were also confirmed are left out.

`delete_targets()` returns every unconfirmed delete with its target as data: the path as written, its location, whether the command follows symlinks, and the severity it was given. The local warnings in [local.md](local.md) use it to look the targets up on the user's machine. `scan()` uses the same parsing, so the two never disagree.

`tier` is ignored: the checks are the same at every tier. `CHECKS_VERSION` goes in the report header as `checks_version` and is bumped whenever a rule is added or changed, because a rule change can change scores.

## What is scanned

Decided: everything is scanned, so a dangerous command is not missed because of where it is written.

| Source | Scanned | Warning check |
|---|---|---|
| Fenced code blocks in `SKILL.md` and other `.md` files | Yes | No |
| Script files (`.sh`, `.bash`, `.zsh`, `.py`, `.js`, `.ts`, `.ps1`, `.psm1`, `.bat`, `.cmd`) | Yes, every line | No |
| Prose in markdown, including inline code (`` `rm -rf` ``) | Yes | Yes |
| `SKILL.md` frontmatter | Yes, every line, and each text value as parsed | Yes |
| Other files that start with `#!` | Yes, every line | No |
| Other file types, such as binaries | No | — |

**Prose** is the ordinary sentences of a markdown file: explanations, warnings, steps written in words. Skills often warn about dangerous commands there, as in "never run `rm -rf` on the project root". Flagging those would make careful skills, which warn about risks, score lower than careless ones that do not. So matches in prose go through a warning check first.

### Nothing the agent sees is skipped

Added in `CHECKS_VERSION` 6. Each of these once left a command unscanned, with Safety 1.0:

- **Bytes that are not UTF-8** are replaced and the file is scanned (a shell runs such a script), with a note in `detail`. NUL bytes are dropped, from `SKILL.md` too: bash ignores them, and in UTF-16 text they separate the letters of every command.
- **Frontmatter** is only `SKILL.md`'s, matched as `Skill.load` matches it. In any other file `---` is a horizontal rule and what follows is ordinary text. `SKILL.md`'s frontmatter is scanned as prose, one key or list item at a time, so a warning or confirmation in one value cannot clear a command in another, or in the text below. Every key and text value is also scanned as parsed, which undoes YAML escapes and folded lines, at its top-level key's line. A key, value or list item that is all there is on its one line is not scanned twice; an entry of a `[...]` or `{...}` is always scanned on its own. A parsed text is held to the same limit on commands per line as a file.
- **A file with another suffix, or none,** that starts with `#!` is scanned as a script.
- **A file over `MAX_FILE_BYTES`** (1,000,000 bytes) is scanned up to that size and gets a CRITICAL `scan-limit` finding, as the rest is unknown ([Scan limits](#scan-limits)).
- **A file that cannot be read** gets the same finding. One that is listed but no longer there is only noted.

**Links are not followed** (version 8 for Windows junctions). A file that is a symlink or a junction, is reached through one, or leads outside the skill is not read; a link that leads outside the skill is a `bundle-symlink` finding. The same test (`metaskill/links.py`) keeps such files out of Cisco's scan copy, configuration evidence and the local warnings' file counts.

### Warning check

Only for matches in prose. A command in a code block or a script is never skipped as a warning; it can only be cleared by the confirmation check below.

1. **Find the sentence** containing the match, splitting at `.`, `!`, `?` and line breaks.
2. **Look for a warning that governs the command.** Either a negation directly before it (never, don't, do not, must not, should not, avoid, not, not to, under no circumstances), optionally with a verb such as "run" or "use", and possibly other commands joined by "or" / "and" — "never run `x`", "do not `x`", "avoid running `x` or `y`". Or a warning label at the start of the sentence (Warning, Caution, Danger, ⚠️) **together with wording that describes the danger**: a negation, or harm words such as "deletes", "permanently", "overwrites", "cannot be undone", "gone". A label on its own is not enough — "Warning: run `x`" or "Caution: enter `x`" only point at the command, so they are instructions, and a listed verb that merely describes the command ("using `x` deletes…") does not make it one either. A negation elsewhere in the sentence does not count: "don't hesitate to run `x`" and "don't forget to run `x`" are instructions, and in "Run `rm -rf build/`; never touch src/" the "never" is about something else.
3. **Warning blocks count too**, with or without a warning word: GitHub alerts (`> [!WARNING]`, `> [!CAUTION]`) and blockquotes starting with **Warning**, **Caution** or **Danger**.
4. **Commands named as examples** are skipped the same way: inside parentheses with "etc." or "e.g.", or right after "such as" / "for example", as in "commands flagged as destructive (`rm -rf`, `git reset --hard`, etc.)". **A delete with a concrete target is never an example**, so "free up space (e.g. `rm -rf ~/Documents`)" is still a finding. Abbreviations like "e.g." and "etc." do not end a sentence.

The rules were tightened in version 3 after testing how a malicious author could hide a delete of the user's files: "Don't hesitate to run `rm -rf ~/Documents`" used to be skipped because the sentence contained "don't", and "Warning: run `rm -rf ~/Documents`" used to be skipped for its label alone. Both now score 0.0, while a genuine warning ("Never run `rm -rf ~/Documents`", or "Warning: `rm -rf ~/Documents` deletes your files") still scores 1.0.

Version 4 closed three more label-path bypasses. Harm words are searched with the code spans masked, so a harm word inside the command itself (`rm -rf ~/lost+found`) no longer makes the sentence a warning. `without`, `can't`/`cannot` and `won't` were dropped from the harm words, because they appear in instructions too ("run `x` without asking", "you can't continue until you run `x`"). And a list extends a governing negation to a later command only when the last separator is a conjunction (`or`/`and`/`nor`), not a bare comma, so "Do not use `--interactive`, `rm -rf ~/` is faster" is a finding.

**Bounds (version 5, MET-42).** Clearing a match (as a warning, an example, or a confirmed command in prose) reads its whole sentence, so a paragraph packed with thousands of commands used to cost commands × sentence length and could stall `evaluate`. Two bounds now cap that work, and both **fail closed**: a match past them is reported as a finding, never skipped.

- `MAX_CLEARED_PER_UNIT` (200): at most 200 prose matches per paragraph are weighed for clearing. Later matches in that paragraph are findings. A flood of warned commands therefore cannot hide a real instruction after it; it only adds findings.
- `MAX_CLEARED_SENTENCE` (10,000 characters): a match in a longer sentence is a finding. The longest sentence in the bundled and Hermes skills is about 2,800 characters (p99 about 400), and the Hermes snapshot is unchanged.
- `MAX_CLEARING_CHARS` (500,000): the sentence characters weighed per skill. The two bounds above cap one paragraph, but a skill can repeat paragraphs that sit at them (about 0.4 s each); past this budget, prose matches are findings. The most any of the 340 real skills weighs is about 8,300.

When either bound is hit, `detail` notes the paragraph ("too many commands in one paragraph or sentence to check for warnings"). Sentence boundaries, the confirmation window and the paragraph text are also computed once per unit rather than once per match. The rest of the scan's cost is bounded by the scan limits below ([Scan limits](#scan-limits)).

A skipped match is not dropped silently. Each one is listed in `detail` with its location, e.g. `skipped as a warning or example: SKILL.md:12 (rm -rf)`, so a reader can check it. At the `deep` tier the LLM review re-checks them and can restore any that are real instructions ([review.md](review.md)).

| Text | Result | Correct? |
|---|---|---|
| "Never run `rm -rf` on the project root." | Skipped | Yes |
| `> [!WARNING]` block mentioning `git push --force` | Skipped | Yes |
| "Clean up by running `rm -rf ~/.cache/x`." | Finding | Yes |
| "Don't forget to run `rm -rf build/`." | Finding | Yes |
| "Don't hesitate to run `rm -rf ~/Documents`." | Finding | Yes |
| "Be careful not to run `rm -rf ~/Documents`." | Skipped | Yes |
| "Warning: run `rm -rf ~/Documents` now." | Finding | Yes |
| "Warning: using `rm -rf ~/Documents` deletes your files." | Skipped | Yes |
| "It is not uncommon to `rm -rf build/` here." | Finding | Yes (a miss in version 1) |
| "Flagged as destructive (`rm -rf`, `git reset --hard`, etc.)" | Skipped as an example | Yes |
| "You would be silly not to run `rm -rf ~/Documents`." | Skipped | No — a static-tier miss; see below |

Keyword matching cannot follow grammar, so some sentences are misjudged at the static tier. The clearest remaining gap is a **double negative**: "There is no reason not to run `x`" or "You would be silly not to run `x`" read as warnings because of the "not to run", yet they instruct the command. A regex cannot separate these from a genuine "be careful not to run `x`" without false-positiving on that common, legitimate phrasing, so the static check leaves them for the layer that can actually judge grammar: at the `deep` tier the **LLM re-check** restores them as findings ([review.md](review.md)). The skipped list in `detail`, the deep re-check and the golden dataset's cases are how these misses are caught and measured.

## Rules

Each rule has a stable ID (the `rule` field), a category, a severity and a fixed message.

Severities are decided. A severity answers: if the agent does this in an otherwise normal skill, how bad is it? It weighs whether the action can be undone, how much it can affect, and whether it harms only the user or others too. Since the safety score is set by the worst finding, one severity decides the whole score:

| Severity | Score if worst |
|---|---|
| CRITICAL | 0.0 |
| HIGH | 0.11 |
| MEDIUM | 0.31 |
| LOW | 0.61 |
| INFO | 1.0 |

The numbers come from the CVSS band ceilings, not hand-picked; see [severity-scale.md](severity-scale.md).

### `destructive-unconfirmed`

| Rule | Matches | Severity |
|---|---|---|
| `recursive-delete` | The forms listed below | Set by the target, below |
| `force-push` | `git push --force`, `git push -f` | HIGH: can destroy other people's work on a shared branch |
| `history-rewrite` | `git reset --hard`, `git clean -fd` | HIGH: permanently discards uncommitted work or untracked files |
| `db-drop-database` | `DROP DATABASE` | CRITICAL: everything at once, like wiping a disk |
| `db-drop-table` | `DROP TABLE`, `TRUNCATE` | HIGH |
| `disk-write` | `dd ... of=/dev/`, `mkfs`, redirect to `/dev/sd*` | CRITICAL |

`git push --force-with-lease` is not matched: it refuses to overwrite work it has not seen.

#### Recursive-delete forms

| Where | Matched |
|---|---|
| Shell | `rm` with `-r`, `-R` or `--recursive`; `find ... -delete` |
| Python | `shutil.rmtree(...)`, `rmtree(...)`, with or without a space before `(`; through `import shutil as s` or `from shutil import rmtree as nuke` in the same file. Another object's `rmtree` (`sftp.rmtree`) and `def rmtree(` are not matched |
| cmd.exe | `rmdir`, `rd`, `del`, `erase` with `/s`, also written together (`rd/s/q`, `/S/Q`) |
| PowerShell | `Remove-Item` and its aliases `ri`, `rd`, `rmdir`, `del`, `erase` with `-Recurse` or any shorter form (`-r`, `-Rec`), also `-Recurse:$true`; not `-Recurse:$false`. (`rm` is read as the shell's) |

**Inside a string** (version 7). In a code block or a script, a delete written in a string ends where the string does: the target of `os.system("rm -rf /")` is `/`. When the string holds no target, what follows it is read too (`"rm -rf " + folder`).

**No written target** (version 7). In a code block or a script, a recursive delete with nothing to delete written after it, as in `find ~ | xargs rm -rf` or `Get-ChildItem $HOME | Remove-Item -Recurse`, deletes what is piped to it: it is a finding with an unknown target (HIGH). Not when it only stands in a comment, or in a string that nothing runs, as in `grep -qE 'rm -rf|...'`. A string given to `sh -c`, `eval`, `ssh`, `Invoke-Expression`, `cmd /c`, `powershell -Command`, `os.system`, `subprocess` or `exec`, and `$(...)` in a double-quoted string, does count.

**Paths** are compared with `/./` and repeated slashes removed, so `~/./Documents` and `~//Documents` are `~/Documents`. A `..` in the middle of a path that starts at `/` or the home folder is resolved; a trailing `..`, and a path whose parts are not all known, are judged as written. A path that climbs above the home folder (`~/../x`) is judged as the home folder's parent (`~/..`).

#### Delete targets

The check cannot see the user's machine, so it cannot know what is actually at a path. It judges the target as written in the command:

| Target | Examples | Severity |
|---|---|---|
| System, home or personal | `/`, `~`, `$HOME`, `/etc`, `/usr`, `/var`, `~/.ssh`, `~/.config`, `~/.gnupg`, `.git`, `..`; the user's personal folders `~/Documents`, `~/Desktop`, `~/Pictures`, `~/Downloads`, `~/Music`, `~/Videos` (also under `/home/<user>`, `/Users/<user>`, `C:\Users\<user>`) | CRITICAL |
| Unguarded variable that may be empty | `rm -rf $DIR/` or `rm -rf "$DIR"/*`: an empty `DIR` turns it into `rm -rf /` | CRITICAL |
| Unknown | `data/`, `~/Documents/x`, `"$OUTPUT_DIR"`, `{{path}}`, `*`, `.`, any absolute path not listed here | HIGH |
| Rebuildable | `build/`, `dist/`, `out/`, `target/`, `node_modules/`, `__pycache__/`, `.cache/`, `.pytest_cache/`, `/tmp/...` | MEDIUM |

Rules for judging a target:

- **Rebuildable only when plain.** A relative path whose last part is in the rebuildable list, such as `build/`, `./dist` or `frontend/node_modules`, or a path under `/tmp/`. A trailing `/*` (the folder's contents) is allowed. Any `..`, other glob, variable or other absolute path makes it unknown. A path ending in `..`, such as `build/../../`, resolves to `..` and is CRITICAL, so it never passes as `build/`.
- **Python home-folder expressions are understood:** `os.path.expanduser("~/x")`, `os.path.join(os.environ["HOME"], "x")` (also `os.getenv` and `USERPROFILE`), `Path.home() / "x"` and `Path("~/x").expanduser()`, also when assigned to a variable first.
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

**Confirmation check.** A match is only a finding if no confirmation step comes **before** it, nearby (version 7: one that comes after no longer counts). Nearby means:

- Earlier on the same line.
- In markdown: an earlier line of the same code block or paragraph, or the paragraph or list item directly before it.
- In scripts: the 5 lines before it.

In code blocks and scripts, a phrase inside a comment or a string does not count: it asks nothing of anyone (`# TODO: dry run support`, `echo 'Are you sure?'`). The step itself still does (`read -p`, `input('Delete? [y/N] ')`). Comments include `/* ... */` in JavaScript and `<# ... #>` in PowerShell. Comments and strings are read one line at a time, so the inside of a multi-line string is not recognised as one.

A backup that is only described, as in "**we** make a backup of the logs weekly", does not count either; "make a backup first" does.

Confirmation means an actual step, not just the word. Any of:

- Asking: "ask the user **before/to confirm/for confirmation**" (asking about the action, not "ask the user for their favourite colour"), "confirm with the user", "get the user's approval", "wait for permission", "are you sure".
- A dry run: "dry run", `--dry-run`, `-WhatIf`, `-n` on `git clean`.
- A backup: "make a backup", "back up first", "back up your files", "back everything up" — "back up" needs an object, so "go back up one level" does not count.
- Flags and code: `-i` or `--interactive` on `rm`, `read -p`, `confirm(`, `Read-Host`, `-Confirm` and `-WhatIf` (not with `:$false`), `set /p`, the `choice /…` command, and `input(` **only when its prompt reads like a yes/no question** (`input('Delete? [y/N] ')`, not `input('project name: ')`).

At the `deep` tier, a delete or other destructive command cleared by one of these phrases is re-checked ([review.md](review.md)), in code and scripts as well as in prose; one cleared by a flag on the command itself (`rm -i`) is not. At most `MAX_CLEARED_PER_UNIT` matches in a code block or script are cleared this way; later ones are reported.

A phrase negated just before it does not count: "no need to confirm", "skip the backup", "do not ask the user". Neither does the bare word: "Confirm the export finished" is not a confirmation step. Version 1 accepted any occurrence of "ask", "confirm" or "backup", which a malicious author could simply include; version 4 further requires the asking, backup and `input(` forms to be about the action (review feedback).

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
| `curl-pipe-shell` | `curl` or `wget` piped into `sh`, `bash`, `zsh`, `python`, also through `sudo` or `env` with flags and variables (`sudo -E bash`, `sudo -u "$USER" bash`) and by path (`/bin/bash`), also `bash <(curl ...)` and `sh -c "$(curl ...)"`. Not when the interpreter gets its own script (`python3 -c '...'`, `python3 parse.py`, `-m`): then the download is only parsed as data | HIGH |
| `insecure-download` | `curl -k`, `--insecure`, `wget --no-check-certificate`, `pip --trusted-host`, downloads over `http://` | MEDIUM |
| `unpinned-git-install` | `pip install git+...` or `npm install` from a git URL with no commit or tag | MEDIUM |
| `unpinned-install` | `pip install name` with no version, `npm install -g name` with no version | LOW |

`unpinned-install` is common in legitimate skills and caps each of them at 0.61 (LOW). It stays: it is a real, minor supply-chain risk, and skills doing the same job are affected equally, so their ranking relative to each other is unchanged. The absolute penalty is larger than under the old placeholder scores (a LOW now costs more); the band-ceiling choice behind that is owned in [severity-scale.md](severity-scale.md).

"Unknown source" in the safety design (an allowlist of trusted hosts) is not in version 1.

### Hidden characters

Characters that render as nothing, in `SKILL.md` and every markdown or script file. A person reading the skill does not see them, but the agent reads them. Added in `CHECKS_VERSION` 2.

| Rule | Matches | Severity |
|---|---|---|
| `hidden-tag-text` | Tag characters (U+E0000 to U+E007F). They mirror ASCII, so a run of them can spell out an instruction only the model sees. The message gives the decoded text | HIGH |
| `bidi-control` | Bidirectional controls (U+202A to U+202E, U+2066 to U+2069), which make text display in a different order from how it is read | MEDIUM |
| `zero-width` | Zero-width space, word joiner and invisible operators (U+200B, U+2060 to U+2064), U+180E, and U+FEFF anywhere but the start of a file | LOW |

All are `excess-scope`: a skill has no reason to hide text from its user. Each rule gives one finding per file, at the first line, with the number of lines when there are more. The quote shows the hidden characters visibly (`⟦hidden text: "..."⟧`, `⟦U+200B⟧`), so the report does not carry the hidden text on.

Not matched, because real skills use them:

- Joiners (U+200C, U+200D), which emoji and several scripts need.
- Flag emoji such as England's, which are a black flag followed by tag characters.
- A byte order mark at the start of a file.
- HTML comments. 14 of the 58 Hermes skills have them, all template notes such as `<!-- What should happen -->`. The LLM review sees comments, marked as hidden ([judge.md](../evaluate/judge.md) 2.3), and judges what they say.

None of the 58 Hermes skills triggers any of the three rules.

### Frontmatter aliases

Added in `CHECKS_VERSION` 3.

| Rule | Matches | Severity |
|---|---|---|
| `frontmatter-aliases` | Frontmatter that holds more than `MAX_FRONTMATTER_VALUES` (100,000) values once its YAML aliases are expanded, or that is nested too deep to count | HIGH |

YAML aliases let a few hundred bytes describe billions of values (`a1: &a1 [*a0, *a0]`, and so on). Loading them is instant, because each alias is the same object. Expanding them is not: Cisco's scanner parses the frontmatter itself, and with 2.2.1 its time doubles with each level, 6 s at 2^24 values and minutes past 2^28. So Cisco is not run on such a skill ([cisco.md](cisco.md)), and without this rule the skill would keep a `partial` score from our own checks alone. A skill has no reason to do this, and dodging a scanner is itself suspicious, so it is `excess-scope` at HIGH, like hidden tag text.

`expanded_size` counts each object once and reuses that count, so it is quick however large the expansion is; a recursive alias adds nothing more. The quote is `more than 100000 values` and the location `SKILL.md:1`: the frontmatter is never printed, since that is what takes minutes. Real skills have a few hundred values, and none of the 58 Hermes skills comes near the limit.

### Scan limits

Added in `CHECKS_VERSION` 5 (MET-42).

| Rule | Matches | Severity |
|---|---|---|
| `scan-limit` | A line with more than `MAX_COMMANDS_PER_LINE` (200) read-ahead command starts, or a skill whose commands need more than `MAX_PARSED_CHARS` (1,000,000 characters) of argument parsing | CRITICAL |

The matchers read ahead along the line from where a command starts: to the end of the command for its arguments, or through a regex such as `curl\b[^\n]*\s-k`. A line packed with commands therefore cost commands × line length: a single 1 MB line of `rm -rf x`, `pip install` or `git push` stalled the scan for minutes to hours, and `SKILL.md` has no size limit. That was a denial of service against the scanner. Each kind of cost is now bounded, by an exact change where one exists and by a deterministic limit where it does not.

**Exact (same results, linear cost).**

- `print(`-style calls take the rest of the line as their arguments. Instead of reading it once per call, the line is read once from the first call's arguments (`_SecretView`) and each call is answered by position. That is provably the same answer unless a call's arguments start inside a string literal, an environment read or a `{...}` group of that reading; only then is the call read on its own, charged to the parse budget. A minified script with dozens of `console.log(` calls on one long line, which took seconds before, now takes one pass.
- A match's quote, the stripped line and the inline-code test are computed once per line, not once per match.
- The variable-assignment patterns match leading whitespace on one line only (`^[^\S\n]*`, so a run of blank lines is not rescanned from every line start), and a Python or PowerShell value's `# comment` is cut in code rather than by a lazy regex that backtracked across long runs of spaces.

Each is tested against the old version on random input (`test_print_calls_read_once_per_line_match_reading_each_call`, `test_assignment_patterns_find_the_same_values_as_before`).

**Limits (fail closed).**

- **Per line.** Some regexes read ahead from every command start, with no cheaper exact form: `git push/reset/clean`, `dd`, `curl`/`wget`, `sh`/`bash`/`zsh` with `<(`, `pip install` over http, `echo`/`printf`, `cat`/`less`/`more`/`head`/`tail`/`bat`, and `input(` in the confirmation check. Before any matching, a line with more than 200 such starts (`_COMMAND_START`, matched with a bare `\b` and case-insensitively, so it counts every start the matchers can see, including `.git push`) is blanked and becomes one `scan-limit` finding. The line keeps its line ending, so line numbers do not move. A line that begins with a code fence keeps the fence plus a word, so it still opens a block where it did and still cannot close one: a bare fence inside a block would close it and turn the code after it into prose, where commands can be cleared as warnings. Hidden characters are still checked on the original text. The most such starts on one line in any real skill is 16.
- **Per skill.** Argument parsing in Python (shell arguments of `rm`, `find`, `ln`, `pip`/`npm install`, `Remove-Item`, `del`; the first argument of `shutil.rmtree(`; a `print(` call read on its own) shares a budget of 1,000,000 characters. Past it nothing more is parsed, and the line where it ran out gets a `scan-limit` finding. Real skills parse a few hundred characters.

**Why a finding, and why CRITICAL.** Running out is not an error: an erroring backend is dropped and the skill is scored by the other backends alone ([score.md](score.md)), so a hostile skill could trip the limit on purpose to skip these checks. And what the unchecked text holds is unknown, so it is scored as the worst case. A wall-clock timeout was rejected for both reasons, and because the same skill must always give the same findings.

**What a limit hides.** Commands on a blanked line, and delete targets past the budget, are not reported individually, and `delete_targets()` (the [local warnings](local.md)) does not list them. The skill's safety score is already the minimum from the CRITICAL `scan-limit`.

**Checked on real skills.** On all 340 skills on disk (the 58 Hermes fixtures and `~/.hermes`), version 5 gives the same findings, delete targets, notes and deep re-check records as version 4, field for field; no limit fires; the scan is about 2% slower. A 900 KB minified script with 60 `console.log(` calls on one line scans in about 1 s with no finding (12 s on version 4).

## Results on real skills

Run on 19 Oct 2026 against the 59 skills in a local `~/.hermes/skills`, static only, then again on 21 Oct with version 3. The hidden-characters rule (version 2) found nothing in any of them:

| Worst finding | Version 1 | Version 3 |
|---|---|---|
| None | 43 | 42 |
| LOW (unpinned installs) | 10 | 10 |
| HIGH | 6 | 7 |

Version 3's stricter warning and confirmation checks first added three skills at HIGH, all from text that mentions a command rather than instructs it. The example rule cleared two (`hermes-agent`'s and `computer-use`'s lists of destructive commands). The one left is `claude-code`: a JSON permissions file whose `deny` list blocks `Bash(rm -rf *)`, and a hook that greps for `rm -rf` in order to block it. Both are in code blocks, which are never skipped; a known false positive.

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
- Each row of the warning-check table above, so a change to the rules shows up in tests.
- Personal folders are CRITICAL; the malicious phrasings ("don't hesitate to run", an example list with a real target, "confirm" or "no need to confirm" near the command) are findings; genuine warnings and real confirmation steps are not.
- A command in a code block next to a warning sentence: still a finding.
- A script with a confirmation 3 lines up (no finding) and 10 lines up (finding).
- Locations and quotes are exact.
- Scan cost (MET-42): sentence lookup, the line-wide `print(` reading and the assignment patterns each give the same results as the old versions on random input; a flood of warned commands past the paragraph bound cannot hide a later instruction, and within it warnings are still skipped; a match in an overlong sentence is reported; a line over the command limit is a CRITICAL `scan-limit` that does not quote the line; a limited fence line neither drops nor closes its code block; `.git push`, `.curl`, `.dd`, `-wget`, `more` and `x.echo` count toward the line limit; a minified script with many `console.log(` calls is not limited and its secret is still found; running out of parse budget is a finding with backend status `ok`; past the skill's clearing budget, warned commands are reported without a scan-limit; fifteen hostile inputs (the issue's reproducer, separator-free and dot-prefixed runs, `pip install` and `print(` runs including indented ones and ones that force re-reading, paragraphs repeated at the clearing bounds, space and blank-line runs) each scan in under 10 s, where each took seconds to hours before.

## Open

- The score values behind each severity are placeholders (open question 3 in the safety design), so the effect of a severity on the score may change even though the severities are decided.
- The rebuildable and system target lists are a first version.
- The warning and exception word lists are a first version, to be tuned against the golden dataset.
- Trusted-host allowlist for `untrusted-source`.
