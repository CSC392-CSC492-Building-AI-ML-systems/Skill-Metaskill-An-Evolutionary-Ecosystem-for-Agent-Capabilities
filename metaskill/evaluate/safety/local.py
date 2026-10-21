"""Warnings about this machine's files; never scored (docs/safety/local.md).

The safety score describes the skill, so it is the same everywhere. This module
looks the skill's delete targets up on the machine running the evaluation and
warns when running the skill here would delete something important. It only
reads file metadata: it never opens a file and never changes anything.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import time
from pathlib import Path

from metaskill.evaluate.models import LocalWarning, Skill
from metaskill.evaluate.safety.checks import DeleteTarget, is_system_path

MANY_FILES = 100
RECENT_DAYS = 7
MAX_ENTRIES = 10_000  # stop counting here and report "at least"
MAX_SECONDS = 1.0  # per target
IMPORTANT_HOME_FOLDERS = {"Documents", "Desktop", "Pictures", "Downloads", "Music", "Videos"}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")
# $VAR, ${VAR}, ${VAR:?msg}, and on Windows %VAR%, $env:VAR, ${env:VAR}
_VARIABLE = re.compile(r"\$\{(?:env:)?([A-Za-z_]\w*)(?::\?[^}]*)?\}|\$(?:env:)?([A-Za-z_]\w*)|%([A-Za-z_]\w*)%")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")  # C:\... or \\server\share
_HOME_VARS = {"HOME", "USERPROFILE"}


def local_warnings(skill: Skill, targets: list[DeleteTarget],
                   config: dict[str, str] | None = None, *,
                   env: dict[str, str] | None = None,
                   home: str | Path | None = None) -> list[LocalWarning]:
    """Warnings for the delete targets that exist here and look important.

    config holds the user's personalization values for placeholders. env and
    home default to this process's environment and home folder; tests pass their own.
    """
    env = dict(os.environ) if env is None else env
    home = Path(home) if home is not None else Path.home()
    warnings = []
    for target in targets:
        path = _resolve(target.target, config or {}, env, home)
        if path is None or not os.path.lexists(path):
            continue
        reasons = _reasons(path, target.follows_links, home)
        if reasons:
            warnings.append(LocalWarning(
                target=target.target,
                resolved=str(path),
                location=target.location,
                reasons=reasons,
                message=(f"`{skill.name}` deletes `{target.target}` without asking "
                         f"({target.location}). On this machine it {_join(reasons)}."),
            ))
    return warnings


def _resolve(raw: str, config: dict[str, str], env: dict[str, str], home: Path) -> Path | None:
    """The path a target names on this machine, or None if that depends on where the
    agent is working (a relative path) or on a value this machine does not have."""
    t = raw.strip().strip("'\"")

    def placeholder(m: re.Match) -> str:
        return config.get(m.group(1), m.group(0))

    def variable(m: re.Match) -> str:
        name = m.group(1) or m.group(2) or m.group(3)
        if name.upper() in _HOME_VARS:
            return str(home)
        return _env_value(env, name) or m.group(0)

    t = _VARIABLE.sub(variable, _PLACEHOLDER.sub(placeholder, t))
    if "{{" in t or "$" in t or re.search(r"%\w+%", t):
        return None  # an unresolved placeholder or variable
    if t == "~" or t.startswith(("~/", "~\\")):
        t = str(home) + t[1:]
    for suffix in ("/*", "/.", "\\*", "\\."):
        while t.endswith(suffix) and len(t) > 3:
            t = t[:-2]
    if any(c in t for c in "*?["):
        return None
    if _WINDOWS_ABSOLUTE.match(t):
        return Path(ntpath.normpath(t))
    if t.startswith("/"):
        return Path(posixpath.normpath(t))
    return None  # relative: depends on the folder the agent is working in


def _env_value(env: dict[str, str], name: str) -> str:
    """An environment variable's value; names are matched in any case, as on Windows."""
    if name in env:
        return env[name]
    return next((v for k, v in env.items() if k.upper() == name.upper()), "")


def _reasons(path: Path, follows_links: bool, home: Path) -> list[str]:
    if path.is_symlink():
        if not follows_links:
            return []  # the delete removes only the link
        real = os.path.realpath(path)
        return [f"is a link to `{os.readlink(path)}`; deleting through it removes `{real}`"]

    reasons = []
    if path == home:
        reasons.append("is your home folder")
    elif path.parent == home and path.name in IMPORTANT_HOME_FOLDERS:
        reasons.append(f"is your {path.name} folder")
    elif is_system_path(str(path)):
        reasons.append("is a system folder")
    if path.is_dir():
        reasons += _contents(path)
    return reasons


def _contents(folder: Path) -> list[str]:
    """Git repository, file count and recent changes, from metadata only."""
    files, repo, recent, capped = 0, False, False, False
    cutoff = time.time() - RECENT_DAYS * 86400
    deadline = time.monotonic() + MAX_SECONDS
    stack = [folder]
    while stack and not capped:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            if files >= MAX_ENTRIES or time.monotonic() > deadline:
                capped = True
                break
            try:
                if entry.name == ".git":  # a folder, or a file in worktrees and submodules
                    repo = True
                    continue
                if entry.is_symlink():
                    continue  # deleting the folder removes the link, not what it points to
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                files += 1
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    recent = True
            except OSError:
                continue

    reasons = []
    if repo:
        reasons.append("is or contains a git repository")
    if files >= MANY_FILES or capped:
        reasons.append(f"contains {'at least ' if capped else ''}{files:,} files")
    if recent:
        reasons.append(f"has files changed in the last {RECENT_DAYS} days")
    return reasons


def _join(reasons: list[str]) -> str:
    return reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " and " + reasons[-1]
