# checks.py

Status: planned.

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
```

`tier` is ignored: the checks are the same at every tier. `CHECKS_VERSION` goes in the report header as `checks_version` and is bumped whenever a rule is added or changed, because a rule change can change scores.

## What is scanned

| Source | Scanned |
|---|---|
| Fenced code blocks in `SKILL.md` and other `.md` files | Yes |
| Script files (`.sh`, `.bash`, `.py`, `.js`, `.ts`, `.ps1`) | Yes, every line |
| Prose in markdown | No |
| Inline code in prose (`` `rm -rf` ``) | No, in version 1 |
| Binary or very large files | No |

**Why not prose:** skills often warn about dangerous commands, as in "never run `rm -rf` on the project root". Matching prose would flag the warning itself. Commands the agent is meant to run are almost always in code blocks or scripts.

## Rules

Each rule has a stable ID (the `rule` field), a category, a severity and a fixed message.

### `destructive-unconfirmed`

| Rule | Matches | Severity |
|---|---|---|
| `recursive-delete` | `rm -r`, `rm -rf`, `rm -fr`, `shutil.rmtree`, `Remove-Item -Recurse` | HIGH |
| `recursive-delete-root` | The above on `/`, `~`, `$HOME` or an unquoted variable followed by `/` | CRITICAL |
| `force-push` | `git push --force`, `git push -f` | HIGH |
| `history-rewrite` | `git reset --hard`, `git clean -fd` | MEDIUM |
| `db-drop` | `DROP TABLE`, `DROP DATABASE`, `TRUNCATE` | HIGH |
| `disk-write` | `dd ... of=/dev/`, `mkfs`, redirect to `/dev/sd*` | CRITICAL |

`git push --force-with-lease` is not matched: it refuses to overwrite work it has not seen.

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
| `env-dump` | `env` or `printenv` with no arguments; `print(os.environ)`; `console.log(process.env)` | HIGH |
| `cat-credentials` | `cat` of `.env`, `~/.aws/credentials`, `~/.netrc`, `*.pem`, `id_rsa` | HIGH |
| `shell-trace` | `set -x` in a script that also reads a secret-named variable | MEDIUM |

Not covered here: secrets written into the skill itself (that is Configuration Cleanliness), and secrets sent to a third party (Cisco's `data_exfiltration`).

### `untrusted-source`

| Rule | Matches | Severity |
|---|---|---|
| `curl-pipe-shell` | `curl` or `wget` piped into `sh`, `bash`, `zsh`, `python` | HIGH |
| `insecure-download` | `curl -k`, `--insecure`, `wget --no-check-certificate`, `pip --trusted-host`, downloads over `http://` | MEDIUM |
| `unpinned-git-install` | `pip install git+...` or `npm install` from a git URL with no commit or tag | MEDIUM |
| `unpinned-install` | `pip install name` with no version, `npm install -g name` with no version | LOW |

"Unknown source" in the safety design (an allowlist of trusted hosts) is not in version 1.

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

Fixture skills in `tests/safety/fixtures/skills/`. These are meant to become the golden dataset's safety cases later.

- `clean/`: no findings.
- One skill per rule that should fire.
- For each `destructive-unconfirmed` rule, a confirmed version that should not fire.
- A skill that only mentions dangerous commands in prose: no findings.
- A script with a confirmation 3 lines up (no finding) and 10 lines up (finding).
- Locations and quotes are exact.

## Open

- Severities in the tables are proposals. They interact with the placeholder score table.
- Whether to scan inline code in prose. Off in version 1 because of warnings like "never run `rm -rf`".
- Trusted-host allowlist for `untrusted-source`.
