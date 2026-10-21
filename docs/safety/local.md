# local.py

Status: built and tested (`tests/safety/test_local.py`). Called through `safety.local_warnings(skill, config_values)`, which gets the targets from `checks.delete_targets()`. On the 59 local Hermes skills it raised no warnings: their only two unconfirmed delete targets are variables that cannot be resolved on this machine.

## Purpose

Warn the user when running a skill **on this machine** would delete something important.

The safety score describes the skill, so it is the same on every machine: a shared skill must not score differently depending on whose folders happen to be checked. But the metaskill plugin runs inside Hermes on the user's own computer, so it can also look at that computer. This file does that, and reports what it finds as warnings to this user. **It never affects the score**, and it is not a backend.

Example output, at the top of the tool's result:

> ⚠️ **Not safe to run on this machine:** `cleanup-skill` deletes `~/Documents/drafts` without asking (`SKILL.md:14`). That folder exists and contains 312 files.

## Interface

```python
@dataclass
class LocalWarning:
    target: str          # the path as written in the skill, e.g. "~/Documents/drafts"
    resolved: str        # the real path on this machine
    location: str        # where the delete is in the skill, e.g. "SKILL.md:14"
    reasons: list[str]   # e.g. ["contains 312 files", "is or contains a git repository"]
    message: str         # one sentence for the user

def local_warnings(skill: Skill, targets: list[DeleteTarget],
                   config: dict[str, str] | None = None, *,
                   env: dict[str, str] | None = None,
                   home: str | Path | None = None) -> list[LocalWarning]: ...
```

`targets` comes from `checks.delete_targets(skill)`. `config` is the user's personalization values, when the skill is a generalized one with placeholders. `env` and `home` default to the real environment and home folder; tests pass their own. `LocalWarning` is defined in `models.py`, since it is part of the report.

## Which targets are checked

Only targets that point to the same place whatever folder the agent is working in:

| Target as written | Checked | Resolved with |
|---|---|---|
| Absolute path: `/data/x` | Yes | As written |
| Home path: `~/Documents`, `$HOME/notes` | Yes | The user's home folder |
| Environment variable: `$OUTPUT_DIR` | Yes, if set | Its value in the user's environment |
| Placeholder: `{{OUTPUT_DIR}}` | Yes, if the user has a value for it | The user's personalization config |
| Windows forms: `C:\Data`, `%USERPROFILE%\x`, `$env:APPDATA\x`, `~\x` | Yes | Drive paths as written; home variables as the home folder; other variables from the environment, matched in any case as on Windows |
| Relative path: `build/`, `data/`, `.\dist` | No | Depends on the folder the agent is in later |

Unresolvable targets are skipped silently; the static finding already covers them.

Only paths for the machine's own system can exist on it: a `C:\...` target is never found on Linux, and is skipped like any missing path. Windows resolution is tested on Linux; the lookups themselves have not yet been run on a Windows machine.

## What counts as important

Each resolved target is looked up. A warning is raised if any of these hold:

| Check | Reason shown |
|---|---|
| It is a symlink | "is a link to `~/thesis`; deleting through it removes `~/thesis`" (only for delete forms that follow links, see [checks.md](checks.md)) |
| It is the home folder, or a top-level home folder: `~/Documents`, `~/Desktop`, `~/Pictures`, `~/Downloads`, `~/Music`, `~/Videos` | "is your Documents folder" |
| It is or contains a git repository (a `.git` folder or file) | "is or contains a git repository" |
| It contains many files | "contains 312 files" (threshold: 100) |
| It contains recently changed files | "has files changed in the last 7 days" |

A target that does not exist raises no warning. Thresholds are a first version.

## Read-only and bounded

Looking at the user's files must be harmless:

- **Metadata only.** It uses `lstat`, `readlink` and directory listings. It never opens or reads a file's contents.
- **Never follows links while counting**, so a link cannot send it walking the whole disk. Links inside a folder are not counted at all: deleting the folder removes the link, not what it points to.
- **Bounded:** counting stops at 10,000 entries and after one second per target, and reports "at least 10,000 files".
- **Never changes anything.** No file is created, moved or deleted.

## Where warnings go

- **Tool output:** at the top, with the other warnings such as "Cisco not installed". This is the main way the user is told.
- **Report:** a `local_warnings` list on each skill, separate from `findings`, marked as specific to this machine. It never enters the scores or the ranking.
- **Privacy:** warnings contain real paths and file counts from the user's machine. If a report is shared, for example as a golden dataset case, `local_warnings` should be removed first.

Two changes outside safety were made for this: a `LocalWarning` type and a `local_warnings` field on `SkillResult` in `models.py`, and the new field in evaluate.md §2.1. Filling it in is the runner's job, which does not exist yet.

## Tests

All tests use a temporary folder as the home directory and a fake environment, so they never look at the real machine.

- Absolute, home, environment-variable and placeholder targets are resolved; relative targets are skipped.
- Each row of the "important" table raises a warning with the right reason.
- A missing target raises no warning.
- A symlink is reported with where it points, but not followed while counting.
- Counting stops at the limit and says "at least".
- No file in the temporary folder is opened, changed or removed (checked by comparing before and after).

## Limits and open questions

- **Only at evaluation time.** The warning is shown when the skill is evaluated, not when the user later runs it. Warning at run time would need a hook in Hermes itself; a separate, larger idea.
- **Relative targets are not checked**, because the folder they refer to is only known when the skill runs.
- **Thresholds and the list of important home folders** are a first version.
- **Personalization config format** comes from the personalize design; this assumes a simple name-to-value mapping.
- **[OPEN]** Whether to also check targets of other destructive rules, such as the repository a `git reset --hard` would run in.
