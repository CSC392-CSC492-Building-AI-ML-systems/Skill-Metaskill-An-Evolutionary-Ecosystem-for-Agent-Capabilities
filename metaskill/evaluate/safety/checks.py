"""Own deterministic checks for the careless side of safety (docs/safety/checks.md).

Rules for destructive-unconfirmed, credential-logging and untrusted-source, plus
symlinks that point outside the skill and characters a person cannot see. No model and no network: the same skill
always gives the same findings.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from bisect import bisect_left, bisect_right
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..judge import mark_hidden
from ..models import Dimension, Finding, SafetyTier, Severity, Skill
from .findings import SEVERITY_RANK, BackendResult, BackendStatus

# Bumped whenever a rule is added or changed; recorded as checks_version.
CHECKS_VERSION = "7"  # 2: hidden characters. 3: frontmatter aliases. 4: personal folders
                      # CRITICAL; stricter warning and confirmation checks. 5: scan-limit
                      # and bounded clearing, so a hostile skill cannot stall the scan
                      # (MET-42). 6: nothing the agent sees is skipped: undecodable bytes,
                      # SKILL.md's frontmatter, `---` blocks and oversized files. 7: more
                      # forms of recursive delete; a confirmation must come before the
                      # command, outside comments and strings

MARKDOWN_SUFFIXES = {".md", ".markdown"}
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".psm1",
                   ".bat", ".cmd"}
MAX_FILE_BYTES = 1_000_000
CONFIRM_LOOKBACK = 5  # script lines before a command searched for a confirmation
MAX_LISTED_SKIPS = 10
# Clearing a prose match (as a warning, an example or a confirmed command) reads its whole
# sentence, so a paragraph packed with commands would cost commands x sentence length
# (MET-42). Past these bounds a match is reported, never cleared: an attacker who floods a
# paragraph gets more findings, not fewer. Real skills stay far below both: the longest
# sentence in the bundled and Hermes skills is about 2,800 characters (p99 about 400).
MAX_CLEARED_PER_UNIT = 200  # prose matches per paragraph that can be cleared
MAX_CLEARED_SENTENCE = 10_000  # characters in a sentence whose matches can be cleared
# Sentence characters weighed for clearing per skill. The two bounds above cap one
# paragraph, but a skill can repeat such paragraphs; this caps the whole skill. The most
# any real skill weighs is about 8,300.
MAX_CLEARING_CHARS = 500_000
# Some matchers' regexes read ahead along the line from where a command starts
# (_COMMAND_START), so a line costs such starts x line length. A line with more than this
# is not checked at all: it becomes one CRITICAL scan-limit finding, since what it hides is
# unknown. The most on one line in any real skill is 16. The count is of words, so
# ordinary English (more, head, tail, cat) adds to it; a line only reaches the limit
# with hundreds of them, which a natural paragraph does not have (MET-42 review).
MAX_COMMANDS_PER_LINE = 200
# Characters the Python-level parsers (shell arguments, call arguments, print arguments)
# may read per skill. Real skills read a few hundred; past this, the rest is not parsed
# and the skill gets a CRITICAL scan-limit finding.
MAX_PARSED_CHARS = 1_000_000
MAX_KEY_CHARS = 200  # how far into a frontmatter line its key's colon is looked for

DESTRUCTIVE = "destructive-unconfirmed"
CREDENTIALS = "credential-logging"
UNTRUSTED = "untrusted-source"
EXCESS_SCOPE = "excess-scope"

MESSAGES = {
    "force-push": "Force push with no confirmation step; can overwrite other people's work",
    "history-rewrite": "Discards uncommitted work or untracked files with no confirmation step",
    "db-drop-database": "Drops a whole database with no confirmation step",
    "db-drop-table": "Drops or empties a table with no confirmation step",
    "disk-write": "Writes directly to a disk or formats it, with no confirmation step",
    "echo-secret": "Prints a secret to the terminal, where it lands in the agent's transcript",
    "print-secret": "Prints or logs a secret, where it lands in the agent's transcript or logs",
    "env-dump": "Dumps every environment variable, including any secrets",
    "cat-credentials": "Prints a credentials or key file",
    "shell-trace": "Shell tracing is on in a script that reads a secret, so the secret can be echoed",
    "curl-pipe-shell": "Runs a script downloaded from the internet without inspecting it",
    "insecure-download": "Downloads without verifying the connection",
    "unpinned-git-install": "Installs from a git repository with no commit or tag pinned",
    "unpinned-install": "Installs a package with no version",
    "bundle-symlink": "The skill contains a symlink pointing outside its own folder",
    "hidden-tag-text": "Invisible tag characters spell out text the agent reads but a person "
                       "does not see",
    "bidi-control": "Bidirectional control characters make the text display differently "
                    "from what the agent reads",
    "zero-width": "Zero-width characters that render as nothing",
    "frontmatter-aliases": "The frontmatter expands to more than 100000 values through YAML "
                           "aliases. That stalls scanners, so Cisco's scanner was not run on it",
    "scan-limit": "Too many commands packed together to check in reasonable time, so part of "
                  "the skill was not checked; it is treated as the worst case",
    "scan-limit-file": "A file is too large or could not be read, so part of the skill was not "
                       "checked; it is treated as the worst case",
}

# How many values the frontmatter may hold once its YAML aliases are expanded. Real skills
# have a few hundred. Cisco's scanner (cisco.py) is not run past it.
MAX_FRONTMATTER_VALUES = 100_000


def expanded_size(value: Any, sizes: dict[int, int] | None = None) -> int:
    """How many values `value` holds with every alias expanded, counting each object once
    and reusing that count, so it is quick however large the expansion is. A cycle (a
    recursive alias) adds nothing more."""
    sizes = {} if sizes is None else sizes
    if isinstance(value, dict):
        children = [*value.keys(), *value.values()]
    elif isinstance(value, (list, tuple, set)):
        children = list(value)
    else:
        return 1
    if id(value) in sizes:
        return sizes[id(value)]
    sizes[id(value)] = 0  # while counting this one, a cycle back to it counts as nothing
    sizes[id(value)] = 1 + sum(expanded_size(child, sizes) for child in children)
    return sizes[id(value)]


def frontmatter_too_large(frontmatter: Any) -> bool:
    """Whether the frontmatter holds more than MAX_FRONTMATTER_VALUES values once its
    aliases are expanded. Nesting too deep to count is too large as well."""
    try:
        return expanded_size(frontmatter) > MAX_FRONTMATTER_VALUES
    except RecursionError:
        return True

# --- Delete targets (checks.md, "Delete targets") ------------------------------

REBUILDABLE = {
    "build", "dist", "out", "target", "node_modules", "__pycache__", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "htmlcov",
}
HOME_FORMS = ("~", "$HOME", "${HOME}")
HOME_CRITICAL = (".ssh", ".config", ".gnupg", "appdata")  # compared case-insensitively
# The user's personal files: deleting one of these folders is as bad as deleting home.
HOME_PERSONAL = ("documents", "desktop", "pictures", "downloads", "music", "videos",
                 "movies", "library", "onedrive")  # macOS Movies/Library, Windows OneDrive
SYSTEM_EXACT = {
    "/", "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/opt",
    "/home", "/root", "/Users", "/System", "/Library", "/Applications",
}
SYSTEM_PREFIXES = ("/etc/", "/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/boot/", "/System/")
ALWAYS_SET = {"HOME", "PWD", "TMPDIR", "USER"}  # never empty, so never the empty-variable case

# Windows: paths are converted to the forward-slash form above before judging
# (_canonical), so the same rules apply. Drive letters and names are compared lowercase.
WINDOWS_HOME_VARS = {"USERPROFILE", "HOME"}
WINDOWS_TEMP_VARS = {"TEMP", "TMP"}
WINDOWS_ALWAYS_SET = {"SYSTEMROOT", "WINDIR", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
                      "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH", "USERNAME", "COMPUTERNAME"}
WINDOWS_SYSTEM_EXACT = {"windows", "program files", "program files (x86)", "programdata", "users"}
WINDOWS_SYSTEM_PREFIXES = ("windows/", "program files/", "program files (x86)/", "programdata/")

_KIND_SEVERITY = {
    "system": Severity.CRITICAL,
    "empty-variable": Severity.CRITICAL,
    "symlink": Severity.CRITICAL,
    "unknown": Severity.HIGH,
    "rebuildable": Severity.MEDIUM,
}
_KIND_TEXT = {
    "system": "a system or home folder",
    "empty-variable": "a variable that could be empty, which would delete from /",
    "symlink": "a symlink that may point outside the project",
    "unknown": "a folder that could hold anything",
    "rebuildable": "a rebuildable folder",
}


@dataclass
class DeleteTarget:
    """One target of an unconfirmed delete, for local.py (docs/safety/local.md)."""

    target: str  # as written in the skill
    location: str  # e.g. "SKILL.md:14"
    quote: str
    follows_links: bool
    kind: str  # system, empty-variable, symlink, unknown or rebuildable
    severity: Severity


@dataclass
class SkippedWarning:
    """A match cleared by the warning, example or confirmation check, for the deep
    review's re-check (docs/safety/review.md, "Re-checking skipped warnings")."""

    location: str  # e.g. "SKILL.md:12"
    rule: str
    snippet: str  # the matched command, e.g. "rm -rf ~/"
    sentence: str  # the sentence containing the match
    paragraph: str  # the paragraph or list item it is in
    finding: Finding  # what it becomes if the re-check restores it
    # Where the match starts in `sentence` and `paragraph`, so the review's window is cut
    # on this match, not on the command's first appearance (docs/safety/review.md).
    sentence_offset: int = 0
    paragraph_offset: int = 0
    # "warning": cleared because the sentence warns against the command; the deep re-check
    # asks instruction-or-warning. "confirmation": cleared because a confirmation step was
    # found; the deep re-check asks whether running it really depends on the user's approval.
    kind: str = "warning"


class ChecksBackend:
    name = "own-checks"

    def available(self) -> bool:
        return True

    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult:
        try:
            findings, _, notes, _ = _run(skill)
        except Exception as e:  # never raise into the caller
            return BackendResult(self.name, BackendStatus.ERROR, tier,
                                 detail=f"{type(e).__name__}: {e}")
        return BackendResult(self.name, BackendStatus.OK, tier, findings, "; ".join(notes))


def delete_targets(skill: Skill) -> list[DeleteTarget]:
    """Every unconfirmed delete target, as data. Same parsing as scan()."""
    return _run(skill)[1]


def skipped_warnings(skill: Skill) -> list[SkippedWarning]:
    """Prose matches cleared as a warning, example or confirmation that would otherwise be
    findings, for the deep tier to re-check. Same parsing as scan()."""
    return _run(skill)[3]


# --- Reading the skill ---------------------------------------------------------


@dataclass
class _Unit:
    """A stretch of text checked as one piece: a code block, a paragraph or list
    item of prose, or a whole script."""

    path: str
    kind: str  # "code", "prose" or "script"
    lines: list[tuple[int, str]]
    warning_block: bool = False
    # From SKILL.md's frontmatter: a confirmation or warning there is its own key's, and
    # the paragraph before does not reach into or out of it.
    frontmatter: bool = False
    # Computed once per unit and reused for every match in it (MET-42): see _text.
    cache: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass
class _Text:
    """A prose unit joined into one string, with its sentence boundaries, so each match
    finds its sentence by bisection instead of rescanning the unit."""

    joined: str
    offsets: list[int]  # where each line starts in joined
    starts: list[int]  # just after each sentence end that is followed by whitespace
    ends: list[int]  # just after each sentence end, including one at the very end
    paragraph: str  # the lines stripped and joined, as the deep re-check shows them
    stripped_offsets: list[int]  # where each stripped line starts in paragraph


def _text(unit: _Unit) -> _Text:
    if "text" not in unit.cache:
        texts = [text for _, text in unit.lines]
        joined = " ".join(texts)
        offsets, at = [], 0
        for t in texts:
            offsets.append(at)
            at += len(t) + 1
        masked = re.sub(r"`[^`]*`", lambda m: "x" * len(m.group(0)), joined)
        # Abbreviations are not sentence ends: "(e.g. `x`)" stays one sentence.
        masked = re.sub(r"\b(?:e\.g|i\.e|etc|vs|cf)\.", lambda m: "x" * len(m.group(0)),
                        masked, flags=re.I)
        stripped = [t.strip() for t in texts]
        stripped_offsets, at = [], 0
        for t in stripped:
            stripped_offsets.append(at)
            at += len(t) + 1
        unit.cache["text"] = _Text(
            joined, offsets,
            [m.end() for m in re.finditer(r"[.!?](?=\s)", masked)],
            [m.end() for m in re.finditer(r"[.!?](?=\s|$)", masked)],
            " ".join(stripped), stripped_offsets)
    return unit.cache["text"]


@dataclass
class _Context:
    """Per-skill facts used to judge delete targets."""

    # path -> name -> every assigned value (None when not a plain literal)
    shell_vars: dict[str, dict[str, list[str | None]]] = field(default_factory=dict)
    py_vars: dict[str, dict[str, list[str | None]]] = field(default_factory=dict)
    links: set[str] = field(default_factory=set)  # names that are or become symlinks


# What the deep re-check is shown for a match in code: the lines just before it, bounded.
CONTEXT_LINES = 20
CONTEXT_CHARS = 2_000


def _cleared_code(hit: _Hit, unit: _Unit, line_index: int, location: str, ctx: _Context,
                  previous: _Unit | None) -> SkippedWarning:
    """A SkippedWarning for a match in code or a script cleared by a confirmation phrase:
    its line, with the lines before it (and the paragraph before a code block), where the
    phrase is."""
    lineno, line = unit.lines[line_index]
    before = [t.strip() for _, t in unit.lines[max(0, line_index - CONTEXT_LINES):line_index]]
    if unit.kind == "code" and previous is not None and previous.kind == "prose":
        before = [t.strip() for _, t in previous.lines[-CONTEXT_LINES:]] + before
    lead = " ".join(before)[-CONTEXT_CHARS:]
    sentence = line.strip()
    offset = max(0, hit.start - (len(line) - len(line.lstrip())))
    return SkippedWarning(
        location, hit.rule, hit.snippet, sentence, f"{lead} {sentence}" if lead else sentence,
        _finding(hit, unit, lineno, line, ctx, []),
        offset, offset + (len(lead) + 1 if lead else 0), "confirmation")


def _skipped_warning(hit: _Hit, unit: _Unit, line_index: int, location: str,
                     ctx: _Context, kind: str = "warning") -> SkippedWarning:
    """A SkippedWarning for a cleared prose match, carrying the match's offset in the
    sentence and paragraph so the review centres its window on this match, not on the
    command's first appearance (docs/safety/review.md). `kind` is "warning" or
    "confirmation" and picks the question the deep re-check asks."""
    joined, start, position, end = _sentence_span(unit, line_index, hit.start)
    sentence_raw = joined[start:end]
    sentence = sentence_raw.strip()
    sentence_offset = (position - start) - (len(sentence_raw) - len(sentence_raw.lstrip()))
    text = _text(unit)
    paragraph = text.paragraph
    lineno, line_text = unit.lines[line_index]
    lead = len(line_text) - len(line_text.lstrip())
    paragraph_offset = text.stripped_offsets[line_index] + max(0, hit.start - lead)
    return SkippedWarning(
        location, hit.rule, hit.snippet, sentence, paragraph,
        _finding(hit, unit, lineno, line_text, ctx, []),
        max(0, sentence_offset), max(0, paragraph_offset), kind)


@dataclass
class _Budget:
    """MAX_PARSED_CHARS for one scan (MET-42). Deterministic, unlike a timeout, so the
    same skill still always gives the same findings, and running out is a finding rather
    than an error: an error would drop this backend and leave the skill scored by the
    others alone."""

    left: int = MAX_PARSED_CHARS
    where: tuple[str, int] = ("SKILL.md", 0)  # the line being parsed
    ran_out_at: tuple[str, int] | None = None
    # Sentence characters left to weigh for clearing (MAX_CLEARING_CHARS). Running out is
    # not a scan-limit: later prose matches are just reported, as past MAX_CLEARED_PER_UNIT.
    clearing_left: int = MAX_CLEARING_CHARS

    def take(self, chars: int) -> bool:
        """Spend chars; False (and nothing more is parsed) once the budget is gone."""
        if self.ran_out_at is not None:
            return False
        if chars > self.left:
            self.ran_out_at, self.left = self.where, 0
            return False
        self.left -= chars
        return True


# Set for the length of one scan; None outside one, so helpers called on their own
# (in tests) parse without a limit.
_BUDGET: ContextVar[_Budget | None] = ContextVar("_BUDGET", default=None)


def _parse(chars: int) -> bool:
    budget = _BUDGET.get()
    return budget is None or budget.take(chars)


def _parsing_stopped() -> bool:
    budget = _BUDGET.get()
    return budget is not None and budget.ran_out_at is not None


def _parsing_at(path: str, lineno: int) -> None:
    budget = _BUDGET.get()
    if budget is not None:
        budget.where = (path, lineno)


# Where a matcher's regex reads ahead along the line from a command: git push/reset/clean,
# dd, curl/wget (piped or insecure), sh/bash/zsh <(...), pip install over http, echo,
# cat-like commands and input(. These are the only per-command costs not otherwise bounded:
# argument parsing (rm, find, ln, shutil.rmtree, pip/npm install) is charged to
# MAX_PARSED_CHARS, and print( calls are read once per line (_SecretView). Matched with a
# bare \b and case-insensitively, so it counts every start the matchers can see (they
# exclude more before a word, e.g. `-` or `.`), never fewer.
_COMMAND_START = re.compile(
    r"\b(?:git|dd|curl|wget|sh|bash|zsh|pip3?|Invoke-WebRequest|iwr|echo|printf"
    r"|cat|less|more|head|tail|bat)\b|\binput\s*\(", re.I)


def _limit_lines(files: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[Finding]]:
    """Blank every line with more than MAX_COMMANDS_PER_LINE command starts, and return a
    CRITICAL scan-limit finding for each (MET-42). The line keeps its line ending, so line
    numbers do not move, and a code fence keeps its marker, so a code block does not turn
    into prose (where commands can be cleared as warnings)."""
    out, findings = [], []
    for path, text in files:
        lines = text.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            # Every start is at least two characters, so a shorter line cannot be over.
            if len(line) <= 2 * MAX_COMMANDS_PER_LINE:
                continue
            count = sum(1 for _ in zip(range(MAX_COMMANDS_PER_LINE + 1), _COMMAND_START.finditer(line)))
            if count <= MAX_COMMANDS_PER_LINE:
                continue
            body = line.rstrip("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
            fence = _FENCE.match(body)
            # A fence keeps its marker plus text, so it still opens a block where it did,
            # and still cannot close one: a bare marker inside a block would close it and
            # turn the code after it into prose (MET-42 review).
            lines[i] = (fence.group(0) + " not-checked" if fence else "") + line[len(body):]
            changed = True
            findings.append(_scan_limit(path, i + 1,
                                        f"more than {MAX_COMMANDS_PER_LINE} commands on one line"))
        out.append((path, "".join(lines) if changed else text))
    return out, findings


def _scan_limit(path: str, lineno: int, quote: str, message: str = "scan-limit") -> Finding:
    return Finding(Dimension.SAFETY, EXCESS_SCOPE, Severity.CRITICAL, ChecksBackend.name,
                   "scan-limit", quote, f"{path}:{lineno}", MESSAGES[message])


def _run(skill: Skill) -> tuple[list[Finding], list[DeleteTarget], list[str], list[SkippedWarning]]:
    budget = _Budget(left=MAX_PARSED_CHARS, clearing_left=MAX_CLEARING_CHARS)  # read now, so tests can lower them
    token = _BUDGET.set(budget)
    try:
        return _scan(skill, budget)
    finally:
        _BUDGET.reset(token)
        _backticks.cache_clear()  # do not keep a long line alive after the scan
        _stripped.cache_clear()
        _content_end.cache_clear()
        _quoted_spans.cache_clear()


def _scan(skill: Skill, budget: _Budget) -> tuple[list[Finding], list[DeleteTarget], list[str], list[SkippedWarning]]:
    notes: list[str] = []
    raw_files, unchecked = _read_files(skill, notes)
    files, limited = _limit_lines(raw_files)
    units = [u for path, text in files for u in _units(path, text)]
    value_units, value_limited = _frontmatter_value_units(skill, files)
    units += value_units
    limited += value_limited
    aliases = {
        path: frozenset([*(f"module:{n}" for n in _SHUTIL_ALIAS.findall(text)),
                         *(f"function:{n}" for n in _RMTREE_ALIAS.findall(text))])
        for path, text in files}

    ctx = _Context()
    for path, text in files:
        ctx.shell_vars[path] = _assignments(_SHELL_ASSIGN, _SHELL_LITERAL, text)
        if Path(path).suffix.lower() in (".bat", ".cmd"):
            ctx.shell_vars[path] = _assignments(_CMD_ASSIGN, _CMD_LITERAL, text)
        elif Path(path).suffix.lower() in (".ps1", ".psm1"):
            ctx.shell_vars[path] = _assignments(_PS_ASSIGN, _PY_LITERAL, text, comments=True)
        ctx.py_vars[path] = {
            name: [_python_path(v) if v is not None else None for v in values]
            for name, values in _assignments(_PY_ASSIGN, _PY_ANY, text, comments=True).items()}
    bundle_links = _bundle_symlinks(skill)
    ctx.links = {_normalize(name) for name, _ in bundle_links} | _created_links(units)

    found: dict[tuple[str, int, str], Finding] = {}
    targets: list[DeleteTarget] = []
    skipped: list[str] = []
    rechecks: list[SkippedWarning] = []

    def report(finding: Finding, path: str, lineno: int) -> None:
        """Keep the most severe finding per rule and line."""
        key = (path, lineno, finding.rule)
        if key not in found or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[found[key].severity]:
            found[key] = finding

    for u_index, unit in enumerate(units):
        previous = units[u_index - 1] if u_index else None
        if previous is not None and (previous.path != unit.path or unit.frontmatter
                                     or previous.frontmatter):
            previous = None
        considered = 0  # prose matches in this unit weighed for clearing (MET-42)
        unbounded = False
        considered_code = 0  # code matches cleared by a phrase, bounded as prose ones are
        for l_index, (lineno, text) in enumerate(unit.lines):
            _parsing_at(unit.path, lineno)
            for hit in _hits(text, unit, aliases.get(unit.path, frozenset())):
                location = f"{unit.path}:{lineno}"
                if unit.kind == "prose" and not _clearable(unit, l_index, hit.start, considered, budget):
                    # Over the bounds: report the match without weighing it, so flooding a
                    # paragraph cannot hide a command or stall the scan.
                    unbounded = True
                    report(_finding(hit, unit, lineno, text, ctx, targets), unit.path, lineno)
                    continue
                considered += unit.kind == "prose"
                phrase = (hit.category == DESTRUCTIVE and not hit.confirmed
                          and _confirmed(unit, l_index, hit.start, previous))
                confirmed = hit.category == DESTRUCTIVE and (hit.confirmed or phrase)
                if unit.kind == "prose" and (_is_warning(unit, l_index, hit.start)
                                             or _is_example(unit, l_index, hit)):
                    skipped.append(f"{location} ({hit.snippet})")
                    # Every cleared prose match is re-checked by the deep tier. A match that
                    # is also a confirmed destructive uses the confirmation question, so a
                    # genuine confirmation that also carries a warning label is not restored
                    # as an instruction.
                    kind = "confirmation" if confirmed else "warning"
                    rechecks.append(_skipped_warning(hit, unit, l_index, location, ctx, kind))
                    continue
                if confirmed:
                    # A confirmed destructive is cleared from the static findings, but a
                    # prose match is routed to the deep re-check with the confirmation
                    # question, so a false confirmation is caught while a genuine one stays
                    # cleared.
                    if unit.kind == "prose":
                        rechecks.append(
                            _skipped_warning(hit, unit, l_index, location, ctx, "confirmation"))
                    elif phrase:  # a flag on the command itself needs no second opinion
                        if considered_code >= MAX_CLEARED_PER_UNIT:
                            report(_finding(hit, unit, lineno, text, ctx, targets),
                                   unit.path, lineno)  # past the bound: not cleared
                            continue
                        considered_code += 1
                        rechecks.append(
                            _cleared_code(hit, unit, l_index, location, ctx, previous))
                    continue
                report(_finding(hit, unit, lineno, text, ctx, targets), unit.path, lineno)
        if unbounded:
            notes.append(f"{unit.path}:{unit.lines[0][0]}: commands not checked for warnings "
                         "(too many in the paragraph or the skill, or in an overlong sentence); "
                         "reported as found")
        if unit.kind in ("code", "script"):
            for f in _shell_trace(unit):
                found.setdefault((unit.path, int(f.location.rsplit(":", 1)[1]), f.rule), f)

    # The original text: hidden characters are matched in one linear pass, and a limited
    # line must not hide them.
    for path, text in raw_files:
        for f in _hidden_characters(path, text):
            found.setdefault((path, int(f.location.rsplit(":", 1)[1]), f.rule), f)

    for f in [*limited, *unchecked]:
        found[(f.location.rsplit(":", 1)[0], int(f.location.rsplit(":", 1)[1]), f.rule)] = f
    if budget.ran_out_at is not None:
        path, lineno = budget.ran_out_at
        found.setdefault((path, lineno, "scan-limit"), _scan_limit(
            path, lineno, f"more than {MAX_PARSED_CHARS} characters of commands to parse"))

    if frontmatter_too_large(skill.frontmatter):
        # Never quote the frontmatter: printing it is what takes minutes.
        found[("SKILL.md", 1, "frontmatter-aliases")] = Finding(
            Dimension.SAFETY, EXCESS_SCOPE, Severity.HIGH, ChecksBackend.name,
            "frontmatter-aliases", f"more than {MAX_FRONTMATTER_VALUES} values", "SKILL.md:1",
            MESSAGES["frontmatter-aliases"])

    for name, pointing_to in bundle_links:
        found[(name, 0, "bundle-symlink")] = Finding(
            Dimension.SAFETY, EXCESS_SCOPE, Severity.MEDIUM, ChecksBackend.name,
            "bundle-symlink", f"{name} -> {pointing_to}", name, MESSAGES["bundle-symlink"])

    if skipped:
        shown = ", ".join(skipped[:MAX_LISTED_SKIPS])
        more = len(skipped) - MAX_LISTED_SKIPS
        notes.insert(0, f"skipped as a warning or example: {shown}"
                     + (f" and {more} more" if more > 0 else ""))
    return list(found.values()), targets, notes, rechecks


# --- Hidden characters (checks.md, "Hidden characters") --------------------------

# A flag emoji such as England's is a black flag followed by tag characters; it is not
# hidden text.
_TAG_FLAG = re.compile("\U0001f3f4[\U000e0020-\U000e007e]+\U000e007f")
_HIDDEN = [
    ("hidden-tag-text", Severity.HIGH, re.compile("[\U000e0000-\U000e007f]+")),
    ("bidi-control", Severity.MEDIUM, re.compile("[\u202a-\u202e\u2066-\u2069]")),
    # Joiners (U+200C, U+200D) are left out: emoji and several scripts need them.
    ("zero-width", Severity.LOW, re.compile("[\u200b\u2060-\u2064\u180e\ufeff]")),
]
_HIDDEN_QUOTE_CHARS = 200


def _hidden_characters(path: str, text: str) -> list[Finding]:
    """One finding per kind of invisible character in a file, at its first line.

    HTML comments are not reported: real skills use them for template notes. The LLM
    review sees them, marked as hidden (judge.mark_hidden).
    """
    findings = []
    lines = text.removeprefix("\ufeff").split("\n")  # a BOM at the start is not hidden
    for rule, severity, pattern in _HIDDEN:
        hits = [(n, line) for n, line in enumerate(lines, 1)
                if pattern.search(_TAG_FLAG.sub("", line))]
        if not hits:
            continue
        lineno, line = hits[0]
        message = MESSAGES[rule]
        if rule == "hidden-tag-text":
            run = pattern.search(_TAG_FLAG.sub("", line)).group(0)
            decoded = "".join(chr(ord(c) - 0xE0000) for c in run
                              if 0xE0020 <= ord(c) <= 0xE007E)
            message += f": {json.dumps(decoded[:_HIDDEN_QUOTE_CHARS])}"
        if len(hits) > 1:
            message += f" ({len(hits)} lines)"
        findings.append(Finding(
            Dimension.SAFETY, EXCESS_SCOPE, severity, ChecksBackend.name, rule,
            # Shown visibly, so the report does not carry the hidden text on.
            mark_hidden(line.strip())[:_HIDDEN_QUOTE_CHARS], f"{path}:{lineno}", message))
    return findings


def _read_files(skill: Skill, notes: list[str]) -> tuple[list[tuple[str, str]], list[Finding]]:
    """(relative path, text) for SKILL.md, every markdown or script file and every other
    file that starts with `#!`, and a CRITICAL scan-limit finding for each one that could
    only be checked in part, or not at all.

    Files that are symlinks are not read, so a link cannot make the check read
    something outside the skill.
    """
    files, unchecked = [("SKILL.md", skill.body.replace("\x00", ""))], []
    for rel in skill.files:
        rel = Path(rel)
        name, path = rel.as_posix(), Path(skill.path) / rel
        if (rel.suffix.lower() not in MARKDOWN_SUFFIXES | SCRIPT_SUFFIXES
                and not _has_shebang(path)):
            continue
        try:
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:  # nothing there for the agent to read either
            notes.append(f"could not read {name}: FileNotFoundError")
            continue
        except OSError as e:
            notes.append(f"could not read {name}: {type(e).__name__}")
            unchecked.append(_scan_limit(name, 1, "the file could not be read",
                                         "scan-limit-file"))
            continue
        if len(raw) > MAX_FILE_BYTES:
            raw = _whole_characters(raw[:MAX_FILE_BYTES])
            notes.append(f"{name}: only the first {MAX_FILE_BYTES} bytes were checked")
            unchecked.append(_scan_limit(name, 1, f"more than {MAX_FILE_BYTES} bytes",
                                         "scan-limit-file"))
        files.append((name, _decode(name, raw, notes)))
    return files, unchecked


# A `#!` line, also after a byte-order mark or blank space: the system then ignores it,
# but bash still runs such a file as a shell script.
_SHEBANG = re.compile(r"\ufeff?\s*#!")
_SHEBANG_BYTES = 256  # how far into a file it is looked for


def _has_shebang(path: Path) -> bool:
    """Whether a file with another suffix, or none, is a script all the same: it starts
    with `#!`, so it can be run by its name. Links are not read."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("rb") as stream:
            start = stream.read(_SHEBANG_BYTES).decode("utf-8", errors="replace")
            return _SHEBANG.match(start) is not None
    except OSError:
        return False


def _whole_characters(raw: bytes) -> bytes:
    """raw without a UTF-8 character that was cut off at its end, so that cutting a file
    to size does not make it look like it is not UTF-8."""
    for back in range(1, min(4, len(raw)) + 1):
        byte = raw[-back]
        if byte & 0xC0 != 0x80:  # the first byte of the last character
            length = 1 if byte < 0xC0 else 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return raw[:-back] if length > back else raw
    return raw


def _decode(name: str, raw: bytes, notes: list[str]) -> str:
    """The file's text, whatever its bytes. A shell or an interpreter runs a script with a
    byte that is not UTF-8, so such bytes are replaced and the rest is scanned. NUL bytes
    are dropped: bash ignores them, and in UTF-16 text they sit between the letters of
    every command. Line endings are read as Path.read_text reads them."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        notes.append(f"{name}: not valid UTF-8; scanned with the bad bytes replaced")
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


# The frontmatter as Skill.load reads it (models.py): only SKILL.md has one.
_FRONTMATTER = re.compile(r"---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.DOTALL)
_YAML_KEY = re.compile(r"^(?:[^\s#][^:]*:(?:\s|$)|\s*-\s)")
# A line that holds one `key: value` pair or one list item, and what it holds.
_PAIR = re.compile(r"(?:-[ \t]+)?([^\s:#\[{\-][^:]*):[ \t]+(.*)")
_ITEM = re.compile(r"\s*-[ \t]+(.*)")


def _frontmatter_units(path: str, lines: list[str]) -> list[_Unit]:
    """The frontmatter as prose, one unit for each key or list item with the lines that
    continue it, so that a warning in one value cannot clear a command in another."""
    units: list[_Unit] = []
    block: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if block and (not stripped or stripped == "---" or _YAML_KEY.match(line)):
            units.append(_Unit(path, "prose", block, frontmatter=True))
            block = []
        if stripped and stripped != "---":
            block.append((lineno, line))
    if block:
        units.append(_Unit(path, "prose", block, frontmatter=True))
    return units


def _is_all_of(line: str, value: str) -> bool:
    """Whether a frontmatter line already reads as value does: value is its key, its whole
    value or its whole list item, perhaps quoted. Not one entry of a [...] or {...} on the
    line, where another entry's warning or confirmation would apply to it; and not with a
    comment or an escape, as the line then says more, or something else."""
    if "#" in line or "\\" in line:
        return False
    line = line.rstrip()
    held: list[str] = []
    if pair := _PAIR.match(line):
        held += pair.groups()
    if item := _ITEM.match(line):
        held.append(item[1])
    return any(h in (value, f'"{value}"', f"'{value}'") for h in held)


def _key_lines(block: str) -> dict[str, int]:
    """{what stands before a colon at the start of a line: the first such line}, read once,
    so that finding each key's line does not take a search of the block per key."""
    found: dict[str, int] = {}
    for lineno, line in enumerate(block.split("\n"), 1):
        at = line.find(":", 0, MAX_KEY_CHARS)
        while at != -1:
            key = line[:at].rstrip(" \t")
            if len(key) > 1 and key[0] == key[-1] and key[0] in "\"'":
                key = key[1:-1]  # a quoted key, as it is parsed
            found.setdefault(key, lineno)
            at = line.find(":", at + 1, MAX_KEY_CHARS)
    return found


def _frontmatter_value_units(skill: Skill, files: list[tuple[str, str]]
                             ) -> tuple[list[_Unit], list[Finding]]:
    """Every text in the parsed frontmatter, keys included, as prose reported at its
    top-level key's line: the agent is shown it parsed, so escapes and folded lines are
    undone. Each text is held to the same limit on commands per line as a file; the
    findings for the lines over it come with the units."""
    text = dict(files).get("SKILL.md", "")
    match = _FRONTMATTER.match(text.removeprefix("\ufeff"))
    if match is None or frontmatter_too_large(skill.frontmatter):
        return [], []  # too large has a finding of its own, and is not walked
    block = text[:match.end()]
    # The one-line `key: value` units, by line: such a value is already scanned as it is.
    one_line = {unit.lines[0][0]: unit.lines[0][1]
                for unit in _frontmatter_units("SKILL.md", block.splitlines())
                if len(unit.lines) == 1}
    units: list[_Unit] = []
    limited: list[Finding] = []
    containers: set[int] = set()
    done: set[str] = set()  # aliases repeat a string; scan it once

    def scan(value: str, lineno: int) -> None:
        if not value.strip() or value in done:
            return
        if _is_all_of(one_line.get(lineno, ""), value.strip()):
            return
        done.add(value)
        lines = [line for line in value.replace("\x00", "").splitlines() if line.strip()]
        [(_, kept)], over = _limit_lines([("SKILL.md", "\n".join(lines))])
        limited.extend(_scan_limit("SKILL.md", lineno, f.quote) for f in over[:1])
        kept_lines = [line for line in kept.splitlines() if line.strip()]
        if kept_lines:
            units.append(_Unit("SKILL.md", "prose", [(lineno, line) for line in kept_lines],
                               frontmatter=True))

    def walk(value: Any, lineno: int) -> None:
        if isinstance(value, bytes):  # !!binary
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            scan(value, lineno)
        elif isinstance(value, (dict, list, tuple, set)) and id(value) not in containers:
            containers.add(id(value))
            children = ([*value.keys(), *value.values()] if isinstance(value, dict)
                        else list(value))
            for child in children:
                walk(child, lineno)

    key_lines = _key_lines(block)
    for key, value in skill.frontmatter.items():
        try:
            lineno = key_lines.get(str(key), 1)
        except ValueError:  # an integer too long for Python to write out, from 0x...
            lineno = 1
        walk(key, lineno)
        walk(value, lineno)
    return units, limited


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_WARNING_BLOCK = re.compile(
    r"^\s*>\s*(?:\[!(?:WARNING|CAUTION|DANGER)\]|\*\*(?:warning|caution|danger)\b"
    r"|(?:warning|caution|danger)\s*:|⚠)", re.I)


def _units(path: str, text: str) -> list[_Unit]:
    suffix = Path(path).suffix.lower()
    if suffix in SCRIPT_SUFFIXES or (suffix not in MARKDOWN_SUFFIXES and _SHEBANG.match(text)):
        return [_Unit(path, "script", list(enumerate(text.splitlines(), 1)))]

    units: list[_Unit] = []
    lines = text.splitlines()
    start = 0
    # Only SKILL.md has frontmatter. In any other file `---` is a horizontal rule, and
    # what follows it is ordinary text.
    if path == "SKILL.md" and (match := _FRONTMATTER.match(text.removeprefix("\ufeff"))):
        start = len(match.group(0).splitlines())
        units += _frontmatter_units(path, lines[:start])

    prose: list[tuple[int, str]] = []
    code: list[tuple[int, str]] = []
    fence = ""

    def flush() -> None:
        if prose:
            units.append(_Unit(path, "prose", list(prose), bool(_WARNING_BLOCK.match(prose[0][1]))))
            prose.clear()

    for i in range(start, len(lines)):
        lineno, line = i + 1, lines[i]
        stripped = line.strip()
        if fence:
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                units.append(_Unit(path, "code", code))
                code, fence = [], ""
            else:
                code.append((lineno, line))
            continue
        match = _FENCE.match(line)
        if match:
            flush()
            fence = match.group(1)
            continue
        if not stripped or stripped.startswith("#"):
            flush()
            if stripped:
                units.append(_Unit(path, "prose", [(lineno, line)]))
            continue
        quoted = stripped.startswith(">")
        if prose and (_LIST_ITEM.match(line) or quoted != prose[-1][1].strip().startswith(">")):
            flush()
        prose.append((lineno, line))
    if fence and code:
        units.append(_Unit(path, "code", code))
    flush()
    return units


# --- Matching ------------------------------------------------------------------


@dataclass
class _Hit:
    rule: str
    category: str
    severity: Severity | None  # None for recursive-delete: set by its targets
    start: int  # column of the match, for the warning check
    snippet: str  # the matched command, for the skipped-warnings list
    confirmed: bool = False  # a confirmation flag on the command itself (rm -i, git clean -n)
    targets: list[tuple[str, bool]] = field(default_factory=list)  # (target, follows links)
    py_var: bool = False  # shutil.rmtree(variable): resolve as a Python variable


def _command_end(text: str, start: int = 0) -> int:
    """Where a command that starts at `start` ends: ; & | ` ) a newline or a comment,
    outside quotes. Scans in place rather than on a copy of the rest of the line, so many
    commands on one long line stay linear (MET-42)."""
    quote = ""
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in ";&|`)\n" or (c == "#" and i > start and text[i - 1].isspace()):
            return i
    return len(text)


def _args(text: str, end: int, limit: int | None = None) -> list[str]:
    """Shell-like arguments after position end, up to the end of the command. Charged
    to the scan's parse budget; nothing once it has run out (MET-42)."""
    if _parsing_stopped():
        return []
    stop = _command_end(text, end)
    if limit is not None:
        stop = min(stop, limit)
    if not _parse(stop - end):
        return []
    segment = text[end:stop]
    try:
        # Backslashes are path separators on Windows, not escapes: split without
        # POSIX rules there, and quotes are stripped when a target is judged.
        tokens = shlex.split(segment, posix="\\" not in segment)
    except ValueError:
        tokens = segment.split()
    return [t for t in tokens if "<" not in t and ">" not in t]


# Per-line memos for the line being scanned, so each match on a long line does not redo
# them (MET-42). Small, since a line can be 1 MB; cleared after every scan.
@lru_cache(maxsize=4)
def _backticks(text: str) -> list[int]:
    return [m.start() for m in re.finditer("`", text)]


@lru_cache(maxsize=4)
def _stripped(text: str) -> str:
    return text.strip()


@lru_cache(maxsize=4)
def _content_end(text: str) -> int:
    return len(text.rstrip())


def _head(text: str, start: int) -> str:
    """text[start:].strip()[:60] for a match starting on a non-space, without copying
    the rest of the line (or, on an indented line, the whole line) for every match."""
    return text[start:min(start + 60, _content_end(text))]


def _in_inline_code(text: str, col: int) -> bool:
    """An odd number of backticks before col. Looked up rather than counted, so many
    matches on one long line do not each recount it (MET-42)."""
    return bisect_left(_backticks(text), col) % 2 == 1


def _hits(text: str, unit: _Unit, aliases: frozenset[str] = frozenset()) -> list[_Hit]:
    hits: list[_Hit] = []
    prose = unit.kind == "prose"
    hits += _deletes(text, prose, unit.path, aliases)
    hits += _other_destructive(text)
    hits += _credential_hits(text, prose)
    hits += _untrusted_hits(text)
    return hits


_RM = re.compile(r"(?<![\w.-])rm\s+")
_ASKS_FLAG = re.compile(r"-[A-Za-z]*[iI][A-Za-z]*")  # rm -i, -rI
_RM_LETTERS = re.compile(r"-[dfirIRv]+")  # a group of GNU rm's own one-letter flags
# rmtree(...) bare, as `from shutil import rmtree` leaves it, or on a module: `shutil` or a
# name the file imports it as. Another object's rmtree (sftp.rmtree) is not this one.
_RMTREE = re.compile(r"(?<![\w.])(?<!def )(?:(?P<module>[A-Za-z_]\w*)\s*\.\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")
_RMTREE_QUICK = re.compile(r"\brmtree\b")
_SHUTIL_RMTREE = re.compile(r"shutil\s*\.\s*rmtree\s*\(")  # also self.shutil.rmtree
# Bounded, so many imports on one long line stay linear (MET-42).
_SHUTIL_ALIAS = re.compile(
    r"\bimport\s+(?:[\w.]+\s*,\s*){0,50}shutil\s+as\s+([A-Za-z_]\w*)")
_RMTREE_ALIAS = re.compile(  # on one line, or in the parentheses of one import
    r"\bfrom\s+shutil\s+import\s+(?:\([^)#]{0,500}?|[^\n#(]{0,500}?)"
    r"\brmtree\s+as\s+([A-Za-z_]\w*)")
_PY_STR = r"""(?:[rRbBuU]?(?P<q>['"])(?P<s>[^'"]*)(?P=q))"""
# Python expressions for the home folder: os.environ["HOME"], os.getenv("HOME"),
# os.path.expanduser("~"), Path.home() and pathlib.Path.home().
_PY_HOME = (r"(?:os\.environ\s*\[\s*['\"](?:HOME|USERPROFILE)['\"]\s*\]"
            r"|os\.environ\.get\(\s*['\"](?:HOME|USERPROFILE)['\"]\s*\)"
            r"|os\.getenv\(\s*['\"](?:HOME|USERPROFILE)['\"]\s*\)"
            r"|os\.path\.expanduser\(\s*['\"]~['\"]\s*\)"
            r"|(?:pathlib\.)?Path\.home\(\s*\))")
_FIND = re.compile(r"(?<![\w.-])find\s+")
_CMD_DELETE = re.compile(r"(?<![\w.%$-])(?:rmdir|rd|del|erase)(?=\s|/[a-z?](?![\w.]))", re.I)
# One or more cmd.exe switches written together: /s, /S/Q, /a:h. A path such as /home is not.
_CMD_SWITCHES = re.compile(r"(?:/[A-Za-z?](?::[-\w]*)?)+")
# PowerShell's Remove-Item and its aliases. `rm` is an alias too, and is read as rm above.
_PS_DELETE = re.compile(r"(?<![\w.%$-])(?:Remove-Item|ri|rd|rmdir|del|erase)(?![\w-])", re.I)
# A path written onto its parameter: -Path:$HOME, -LiteralPath:C:\x.
_PS_PATH = re.compile(r"-(?:literalpath|path|lp):(.+)", re.I)
# -Recurse, and any shorter form of it, unless it is switched off with :$false.
_PS_RECURSE = re.compile(r"-r(?:e(?:c(?:u(?:r(?:se?)?)?)?)?)?(?::(?!\$?(?:false|0)\b).*)?", re.I)


def _first_argument(text: str, start: int) -> str:
    """The first argument of a call whose "(" ends at start, up to a top-level comma or ")".
    Charged to the parse budget like _args (MET-42)."""
    if _parsing_stopped():
        return ""
    argument = _first_argument_unbudgeted(text, start)
    return argument if _parse(len(argument)) else ""


def _first_argument_unbudgeted(text: str, start: int) -> str:
    depth, quote = 0, ""
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[start:i].strip()
            depth -= 1
        elif c == "," and depth == 0:
            return text[start:i].strip()
    return text[start:].strip()


def _python_path(expr: str, _depth: int = 0) -> str | None:
    """The path a Python expression names, written the way the rules read paths, or
    None if it cannot be worked out: "~/Documents" for os.path.expanduser("~/Documents"),
    os.path.join(os.environ["HOME"], "Documents") or Path.home() / "Documents"."""
    if _depth > 8:  # a deeply nested str(str(...)) is not a real path; stop recursing
        return None
    # Collapse whitespace runs so padding between tokens cannot push a real path over the
    # length guard below (or make the `.+?` patterns backtrack).
    expr = re.sub(r"\s{2,}", " ", expr.strip())
    m = re.fullmatch(_PY_STR, expr)
    if m:
        return m.group("s")
    # The str(...) and + patterns below use `.+?`, which is quadratic on a long expression
    # that does not match, so they are skipped on anything a real path would never reach.
    short = len(expr) <= 500
    # str(<path expr>): the string form of a path, e.g. str(Path.home()).
    wrapped = re.fullmatch(r"str\(\s*(?P<inner>.+?)\s*\)", expr) if short else None
    if wrapped:
        return _python_path(wrapped.group("inner"), _depth + 1)
    m = re.fullmatch(rf"os\.path\.expanduser\(\s*{_PY_STR}\s*\)", expr)
    if m:
        return m.group("s")
    m = re.fullmatch(rf"(?:pathlib\.)?Path\(\s*{_PY_STR}\s*\)\.expanduser\(\s*\)", expr)
    if m:
        return m.group("s")
    if re.fullmatch(_PY_HOME, expr):
        return "~"
    m = (re.fullmatch(rf"os\.path\.join\(\s*{_PY_HOME}(?P<rest>(?:\s*,\s*{_PY_STR})*)\s*\)", expr)
         or re.fullmatch(rf"{_PY_HOME}(?P<rest>(?:\s*/\s*{_PY_STR})+)", expr))
    if m:
        parts = re.findall(r"""['"]([^'"]*)['"]""", m.group("rest"))
        return "/".join(["~", *parts])
    # <path expr> + "/sub" [+ ...]: a path joined to string literals with +.
    m = re.fullmatch(rf"(?P<base>.+?)\s*(?P<rest>(?:\+\s*{_PY_STR}\s*)+)", expr) if short else None
    if m:
        base = _python_path(m.group("base"), _depth + 1)
        if base is not None:
            parts = re.findall(r"""['"]([^'"]*)['"]""", m.group("rest"))
            return base + "".join(parts)
    return None


def _follows(target: str) -> bool:
    return target.endswith("/") or bool(re.search(r"/[^/]*[*?\[]", target))


# Just before a quote: what runs the string that follows, as a command.
_RUNS_STRING = re.compile(
    r"(?:\b(?:ba|z|da|k)?sh[\"']?[\s,]+(?:[\"']?-[A-Za-z]+[\"']?[\s,]+){0,5}[\"']?-[A-Za-z]*c[\"']?[\s,]+"
    r"|\beval\s+|\bssh\s+[^;&|]{0,80}\s"
    r"|\b(?:os\.system|os\.popen|subprocess\.\w+|execSync|exec|system|popen)\s*\(\s*"
    r"|(?i:\b(?:iex|Invoke-Expression)\s+(?:-Command\s+)?"
    r"|\bcmd(?:\.exe)?\s+/[ck]\s+"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+){0,5}-(?:c|Command)\s+))"
    r"[rRbBfF]{0,2}\Z")
_RUNS_STRING_REACH = 120  # characters before the quote the pattern is searched in


def _is_run(text: str, opened: int) -> bool:
    """Whether the string whose quote is at opened is handed to something that runs it."""
    return _RUNS_STRING.search(text, max(0, opened - _RUNS_STRING_REACH), opened) is not None


def _no_target(text: str, start: int, prose: bool, path: str) -> bool:
    """Whether a recursive delete with nothing written to delete still counts: in code it
    does, as it deletes what is piped to it, unless it only stands in a comment, or in a
    string nothing runs (a pattern such as `grep 'rm -rf|...'` names the command without
    running it)."""
    if prose:
        return False
    span = _span_at(path, text, start)
    if span is None:
        return True
    opened, _, kind = span
    if kind == "comment":
        # An unclosed /* on a line with a backtick may well be inside a template literal.
        return ("`" in text and text.startswith("/*", opened)
                and text.find("*/", opened + 2) < 0)
    if kind == "template":
        return True
    # A JavaScript string ends on its line, so one that does not was misread.
    if (Path(path).suffix.lower() in _SLASH_COMMENTS
            and (span[1] - opened < 2 or text[span[1] - 1] != text[opened])):
        return True
    if _is_run(text, opened):
        return True
    # "$(...)" and "`...`" run inside a double-quoted shell string.
    return text[opened] == '"' and (text.find("$(", opened, start) >= 0
                                    or text.find("`", opened, start) >= 0)


# After a string's closing quote: text that goes on with the command, so that the string is
# only part of it: `"rm -rf " + x`, `"rm -rf /tmp/%s" % x`, `"...".format(x)`, `"build/"*`.
_GOES_ON = re.compile(r"\s*(?:\+|%|\.\s*(?:format|concat|join)\b)|[^\s),;\]}|&#<>]")


def _delete_args(text: str, start: int, end: int, prose: bool, path: str,
                 cmd: bool = False) -> list[str]:
    """The arguments of a delete command found at start. One written inside a string ends
    where the string does, so that in `os.system("rm -rf /")` the target is `/`, and the
    code after `os.system("ls | xargs rm -rf")` gives it no flags or targets. Not when
    something is joined to the string, as in `"rm -rf /tmp/" + folder`, or when a string
    with no target is not run there and then, as in `command = "rm -rf"`, or is followed
    by another argument: what follows may then be part of the command."""
    span = None if prose else _span_at(path, text, start)
    if (span and span[2] == "string" and span[0] < start and text[span[1] - 1] == text[span[0]]
            and not _GOES_ON.match(text, span[1])):
        inside = _args(text, end, span[1] - 1)
        if (any(not a.startswith("-") and not (cmd and _CMD_SWITCHES.fullmatch(a))
                for a in inside)
                or _is_run(text, span[0]) and not text.startswith(",", span[1])):
            return inside
    return _args(text, end)


def _rm_asks(flags: list[str]) -> bool:
    """Whether rm with these flags asks before deleting. As GNU rm reads them, the last
    of -i, -I, -f, --interactive and --force wins: `rm -ri -f` does not ask."""
    asks = False
    for flag in flags:
        # Only a word of letters is a flag that asks: not text a quote joined to it.
        flag = flag.rstrip("'\");,}")
        if _RM_LETTERS.fullmatch(flag):
            for letter in flag:
                if letter in "iIf":
                    asks = letter != "f"
        elif flag in ("--force", "--interactive=never", "--interactive=no",
                      "--interactive=none"):
            asks = False
        elif flag.startswith("--interactive") or _ASKS_FLAG.fullmatch(flag):
            asks = True  # also PowerShell's -Confirm and -WhatIf
    return asks


def _deletes(text: str, prose: bool, path: str = "",
             aliases: frozenset[str] = frozenset()) -> list[_Hit]:
    hits = []
    for m in _RM.finditer(text):
        args = _delete_args(text, m.start(), m.end(), prose, path)
        flags, targets, ended = [], [], False
        for a in args:
            if not ended and a == "--":
                ended = True
            elif not ended and a.startswith("-"):
                flags.append(a)
            else:
                targets.append(a)
        recursive = any(f == "--recursive" or (not f.startswith("--") and re.search("[rR]", f))
                        for f in flags)
        if not recursive:
            continue
        if not targets:
            if not prose and not _no_target(text, m.start(), prose, path):
                continue
            targets = ["(unspecified)"]  # "run `rm -rf` on the build folder", or piped to it
        if prose and not _in_inline_code(text, m.start()):
            targets = targets[:1]  # in plain prose only the next word can be the target
        hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                         f"rm {' '.join(flags)} {targets[0]}", _rm_asks(flags),
                         [(t, _follows(t)) for t in targets]))

    # A file's aliases are looked up per call, not searched for one by one in every line.
    if _RMTREE_QUICK.search(text) or aliases and "(" in text:
        calls = {m.end(): m.start() for m in _SHUTIL_RMTREE.finditer(text)}
        for m in _RMTREE.finditer(text):
            module, name = m.group("module"), m.group("name")
            if not (name == "rmtree" and (module is None or module == "shutil"
                                          or f"module:{module}" in aliases)
                    or module is None and f"function:{name}" in aliases):
                continue
            if (prose and module is None and text[m.end("name"):m.end() - 1]
                    and not _in_inline_code(text, m.start())):
                continue  # "use rmtree (see the docs)" is a sentence, not a call
            calls.setdefault(m.end(), m.start())
        for end, start in sorted(calls.items()):
            argument = _first_argument(text, end)
            path_ = _python_path(argument)
            is_name = path_ is None and re.fullmatch(r"[A-Za-z_]\w*", argument) is not None
            hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, start,
                             f"shutil.rmtree({argument})",
                             targets=[(path_ if path_ is not None else argument
                                       or "(unspecified)", False)],
                             py_var=is_name))

    powershell: set[int] = set()  # where a delete was read as PowerShell, not as cmd.exe
    for m in _PS_DELETE.finditer(text):
        args = _delete_args(text, m.start(), m.end(), prose, path, cmd=True)
        # A quote left on the last word closes the string the command is in.
        if not any(_PS_RECURSE.fullmatch(a.rstrip("'\"")) for a in args):
            continue
        # rd, del and the rest are cmd.exe's too, whose switches are not targets. To
        # Remove-Item and ri, `/a/b` is a path.
        cmd_too = m.group(0).lower() not in ("remove-item", "ri")
        targets = [p[1] if (p := _PS_PATH.fullmatch(a)) else a for a in args
                   if _PS_PATH.fullmatch(a) or not a.startswith("-")
                   and not (cmd_too and _CMD_SWITCHES.fullmatch(a))]
        if not targets:
            if not _no_target(text, m.start(), prose, path):
                continue
            targets = ["(unspecified)"]
        powershell.add(m.start())
        hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                         f"{m.group(0)} {targets[0]} -Recurse",
                         any(a.lower() in ("-confirm", "-whatif") for a in args),
                         [(t, True) for t in targets]))

    for m in _CMD_DELETE.finditer(text):
        if m.start() in powershell:
            continue
        args = _delete_args(text, m.start(), m.end(), prose, path, cmd=True)
        switches = [a for a in args if _CMD_SWITCHES.fullmatch(a.rstrip("'\""))]
        if not any(part.lower() == "s" for a in switches for part in a.split("/")):
            continue  # not recursive
        targets = [a for a in args if not a.startswith("/")]
        if not targets:
            if not _no_target(text, m.start(), prose, path):
                continue
            targets = ["(unspecified)"]
        hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                         f"{m.group(0)} /s {targets[0]}",
                         targets=[(t, False) for t in targets]))

    for m in _FIND.finditer(text):
        args = _args(text, m.end())
        if "-delete" not in args:
            continue
        follow_all = False
        while args and args[0] in ("-L", "-H", "-P"):
            follow_all = follow_all or args[0] in ("-L", "-H")
            args = args[1:]
        starts = []
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            starts.append(a)
        if starts:
            hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                             f"find {starts[0]} -delete",
                             targets=[(s, follow_all or s.endswith("/")) for s in starts]))
    return hits


_DESTRUCTIVE_PATTERNS = [
    ("force-push", Severity.HIGH, re.compile(
        r"(?<![\w-])git\s+push\b[^\n;&|`]*?\s(?:--force(?!-with-lease)\b|-[a-zA-Z]*f\b)")),
    ("history-rewrite", Severity.HIGH, re.compile(r"(?<![\w-])git\s+reset\b[^\n;&|`]*--hard\b")),
    ("history-rewrite", Severity.HIGH, re.compile(
        r"(?<![\w-])git\s+clean\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*f[a-zA-Z]*")),
    ("db-drop-database", Severity.CRITICAL, re.compile(r"\bDROP\s+(?:DATABASE|SCHEMA)\b", re.I)),
    ("db-drop-table", Severity.HIGH, re.compile(
        r"\bDROP\s+TABLE\b|\bTRUNCATE\s+TABLE\b|\bTRUNCATE\s+\w", re.I)),
    ("disk-write", Severity.CRITICAL, re.compile(
        r"(?<![\w-])dd\b[^\n]*\bof=/dev/|(?<![\w-])mkfs(?:\.\w+)?\b"
        r"|>\s*/dev/(?:sd[a-z]|nvme\d|hd[a-z]|disk\d)")),
]


def _other_destructive(text: str) -> list[_Hit]:
    hits = []
    for rule, severity, pattern in _DESTRUCTIVE_PATTERNS:
        for m in pattern.finditer(text):
            keyword = m.group(0).split()[0]
            if rule == "db-drop-table" and keyword.lower() == "truncate" \
                    and not keyword.isupper() and "table" not in m.group(0).lower():
                continue  # "truncate the file" in prose, not SQL
            dry_run = rule == "history-rewrite" and "clean" in m.group(0) \
                and re.search(r"\s-[a-zA-Z]*n", m.group(0)) is not None
            hits.append(_Hit(rule, DESTRUCTIVE, severity, m.start(), m.group(0).strip(), dry_run))
    return hits


_SECRET_PARTS = {"KEY", "APIKEY", "TOKEN", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS",
                 "CREDENTIAL", "CREDENTIALS", "AUTH"}
# A name ending in one of these is about a secret, not the secret: TOKEN_PATH, auth_url.
_NOT_SECRET_ENDINGS = {"PATH", "FILE", "FILENAME", "DIR", "URL", "URI", "NAME", "METHOD", "TYPE",
                       "ENV", "VAR", "HEADER", "FIELD", "PREFIX", "LENGTH", "LEN", "COUNT", "ID",
                       "EXPIRY", "EXPIRES", "SCOPE", "SCOPES", "STATUS"}


def is_secret_name(name: str) -> bool:
    """True for names like API_KEY, apiKey, GITHUB_TOKEN or DB_PASSWORD."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()
    parts = [p for p in re.split(r"[^A-Z0-9]+", words) if p]
    if not parts or parts[-1] in _NOT_SECRET_ENDINGS:
        return False
    return any(part in _SECRET_PARTS for part in parts)


_SHELL_VAR = re.compile(r"\$\{?([A-Za-z_]\w*)")
_ECHO = re.compile(r"(?<![\w.-])(?:echo|printf)\b")
_PRINT = re.compile(
    r"(?<![\w.])(?:print|console\.(?:log|error|info|warn|debug)|logger\.\w+|logging\.\w+|puts)\s*\(")
_ENV_NAMES = re.compile(
    r"""(?:environ(?:\.get)?\s*[\[(]\s*|getenv\s*\(\s*)['"]([A-Za-z_]\w*)['"]|process\.env\.([A-Za-z_]\w*)""")
_STRING = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")
_ENV_DUMP_LINE = re.compile(r"^\s*(?:\$\s+)?(?:sudo\s+)?(?:env|printenv)\s*(?:$|[|;&>#])")
_ENV_DUMP_CODE = re.compile(
    r"print\(\s*(?:dict\(\s*)?os\.environ\s*\)?\s*\)|console\.log\(\s*process\.env\s*\)"
    r"|json\.dumps\(\s*(?:dict\(\s*)?os\.environ")
_CAT_CREDENTIALS = re.compile(
    r"(?<![\w.-])(?:cat|less|more|head|tail|bat)\s+[^\n|;&`]*?"
    r"(?:(?:^|(?<=[\s/]))\.env(?=$|[\s;|&`'\"])|\.aws/credentials|\.netrc\b|\.git-credentials\b"
    r"|\.pgpass\b|\.pem\b|id_(?:rsa|ed25519|ecdsa)(?!\.pub)\b)")


_BRACES = re.compile(r"\{([^{}]*)\}")  # f-strings and template literals
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def _names_secret(args: str) -> bool:
    """Whether the arguments of a print call name a secret: an environment read, a name
    inside {...}, or a name outside string literals."""
    names = [a or b for a, b in _ENV_NAMES.findall(args)]
    for inner in _BRACES.findall(args):
        names += _IDENTIFIER.findall(inner)
    names += _IDENTIFIER.findall(_STRING.sub(" ", args))
    return any(is_secret_name(n) for n in names)


class _SecretView:
    """_names_secret for every print call on a line in one pass (MET-42).

    Each call's arguments run to the end of the line, so reading them per call cost
    calls x line length: a minified script with a few dozen console.log( calls on one
    long line took seconds. Here the line is read once, from the first call's arguments,
    and secret_from(position) answers for any later call.

    That is exact when the call's arguments do not start inside a string literal, an
    environment read or a {...} group of this view: the patterns have no lookbehind, so
    scanning from such a position finds exactly the view's matches from there on.
    Otherwise secret_from returns None and the caller reads the arguments on their own.
    """

    def __init__(self, text: str, start: int):
        secret_at = [-1]
        strings = [(m.start(), m.end()) for m in _STRING.finditer(text, start)]
        envs = list(_ENV_NAMES.finditer(text, start))
        braces = list(_BRACES.finditer(text, start))
        self.spans = [strings, [(m.start(), m.end()) for m in envs],
                      [(m.start(), m.end()) for m in braces]]
        secret_at += [m.start() for m in envs if is_secret_name(m.group(1) or m.group(2))]
        secret_at += [m.start() for m in braces
                      if any(is_secret_name(n) for n in _IDENTIFIER.findall(m.group(1)))]
        # Names outside string literals, as _STRING.sub(" ", ...) leaves them.
        at = start
        for s, e in strings + [(len(text), len(text))]:
            secret_at += [n.start() for n in _IDENTIFIER.finditer(text, at, s)
                          if is_secret_name(n.group(0))]
            at = e
        self.last_secret = max(secret_at)
        self.starts = [[s for s, _ in spans] for spans in self.spans]

    def secret_from(self, position: int) -> bool | None:
        for spans, starts in zip(self.spans, self.starts):
            i = bisect_left(starts, position)
            if i and spans[i - 1][1] > position:
                return None  # inside one of this view's matches
        return self.last_secret >= position


def _credential_hits(text: str, prose: bool) -> list[_Hit]:
    hits = []
    for m in _ECHO.finditer(text):
        rest = text[m.end():]
        stop = re.search(r"[;&`\n]", rest)
        rest = rest[:stop.start()] if stop else rest
        if "|" in rest:
            continue  # piped into another command, not shown
        redirect = re.search(r">>?\s*(\S+)", rest)
        if redirect:
            if "log" not in redirect.group(1).lower():
                continue  # written to a file such as a config, not a log
            rest = rest[:redirect.start()]
        if any(is_secret_name(v) for v in _SHELL_VAR.findall(rest)):
            hits.append(_Hit("echo-secret", CREDENTIALS, Severity.HIGH, m.start(), _head(text, m.start())))

    prints = list(_PRINT.finditer(text))
    view = _SecretView(text, prints[0].end()) if prints else None
    for m in prints:
        secret = view.secret_from(m.end())
        if secret is None:
            # The arguments start inside a string, an environment read or a {...} group of
            # the line-wide view, where reading from here could pair things differently:
            # read them on their own, as before, charged to the parse budget.
            args = text[m.end():]
            if not _parse(len(args)):
                break
            secret = _names_secret(args)
        if secret:
            hits.append(_Hit("print-secret", CREDENTIALS, Severity.HIGH, m.start(), _head(text, m.start())))

    # Only in code: in prose, `env` is usually a word or a config key, not a command.
    if not prose and (_ENV_DUMP_LINE.match(text) or _ENV_DUMP_CODE.search(text)):
        hits.append(_Hit("env-dump", CREDENTIALS, Severity.HIGH, 0, text.strip()[:60]))

    for m in _CAT_CREDENTIALS.finditer(text):
        hits.append(_Hit("cat-credentials", CREDENTIALS, Severity.HIGH, m.start(), m.group(0).strip()))
    return hits


_SET_X = re.compile(r"(?<![\w-])set\s+-[a-zA-Z]*x|set\s+-o\s+xtrace|(?<![\w-])bash\s+-[a-zA-Z]*x\b")


def _shell_trace(unit: _Unit) -> list[Finding]:
    if not any(is_secret_name(v) for _, line in unit.lines for v in _SHELL_VAR.findall(line)):
        return []
    return [
        Finding(Dimension.SAFETY, CREDENTIALS, Severity.MEDIUM, ChecksBackend.name, "shell-trace",
                line.strip(), f"{unit.path}:{n}", MESSAGES["shell-trace"])
        for n, line in unit.lines if _SET_X.search(line)
    ]


# An interpreter runs what it is piped only when given no script of its own:
# "| bash" runs the download, "| python3 -c '...'" only parses it as data.
_CURL_PIPE = re.compile(
    # sudo and env, with their flags (some taking a separate argument) and variables. The
    # argument cannot start with "-" or hold "=", so a run of words is read one way only,
    # not in exponentially many.
    r"(?<![\w-])(?:curl|wget)\b[^\n|]*\|\s*"
    r"(?:(?:(?:/usr)?/bin/)?(?:sudo|env)"
    r"(?:\s+(?:(?:-[ugp]|--user|--group|--prompt)\s+(?!-)[^\s|;&=<>]+(?=\s)"
    r"|-[\w=.-]+|[A-Za-z_]\w*=[^\s|;&]*))*\s+){0,3}"
    r"(?:(?:/usr)?(?:/local)?/bin/)?"
    r"(?:sh|bash|zsh|dash|ksh|python3?|perl|ruby|node)\b"
    r"(?!\s+(?:-[a-zA-Z]*c\b|-m\b|[^\s-]))"
    r"|(?<![\w-])(?:sh|bash|zsh)\b[^\n]*<\(\s*(?:curl|wget)\b"
    r"|(?:eval|sh\s+-c|bash\s+-c)\s+[\"']?\$\(\s*(?:curl|wget)\b")
_INSECURE = re.compile(
    r"(?<![\w-])curl\b[^\n]*\s(?:--insecure\b|-[a-zA-Z]*k[a-zA-Z]*\b)"
    r"|(?<![\w-])wget\b[^\n]*--no-check-certificate"
    r"|--trusted-host\b"
    r"|(?<![\w-])(?:curl|wget|pip3?\s+install|Invoke-WebRequest|iwr)\b[^\n]*"
    r"\bhttp://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])")
_PIP_INSTALL = re.compile(r"(?<![\w-])(?:pip3?|python3?\s+-m\s+pip|uv\s+pip)\s+install\b")
_NPM_INSTALL = re.compile(r"(?<![\w-])npm\s+(?:install|i|add)\b")
_PIP_VALUE_FLAGS = {"-r", "--requirement", "-c", "--constraint", "-i", "--index-url",
                    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix",
                    "--root", "--trusted-host", "-e", "--editable", "--src", "--python-version",
                    "--platform"}
_ALWAYS_UNPINNED_OK = {"pip", "setuptools", "wheel"}


def _git_url_pinned(url: str) -> bool:
    rest = url.split("://", 1)[-1]
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]  # drop user@ in git+ssh://git@host/...
    return "@" in rest.split("#", 1)[0]


def _untrusted_hits(text: str) -> list[_Hit]:
    hits = []
    for m in _CURL_PIPE.finditer(text):
        hits.append(_Hit("curl-pipe-shell", UNTRUSTED, Severity.HIGH, m.start(), m.group(0).strip()))
    for m in _INSECURE.finditer(text):
        hits.append(_Hit("insecure-download", UNTRUSTED, Severity.MEDIUM, m.start(), m.group(0).strip()[:60]))

    for m in _PIP_INSTALL.finditer(text):
        args = _args(text, m.end())
        git_urls = re.findall(r"git\+[^\s'\"]+", " ".join(args))
        if any(not _git_url_pinned(u) for u in git_urls):
            hits.append(_Hit("unpinned-git-install", UNTRUSTED, Severity.MEDIUM, m.start(), m.group(0)))
        if _unpinned_pip(args):
            hits.append(_Hit("unpinned-install", UNTRUSTED, Severity.LOW, m.start(), m.group(0)))

    for m in _NPM_INSTALL.finditer(text):
        args = _args(text, m.end())
        packages = [a for a in args if not a.startswith("-")]
        git = [a for a in packages if a.startswith(("github:", "git+", "git://", "https://github.com/"))]
        if any("#" not in a for a in git):
            hits.append(_Hit("unpinned-git-install", UNTRUSTED, Severity.MEDIUM, m.start(), m.group(0)))
        if ("-g" in args or "--global" in args) and any(
                a not in git and "@" not in a[1:] and not a.startswith((".", "/", "{{")) for a in packages):
            hits.append(_Hit("unpinned-install", UNTRUSTED, Severity.LOW, m.start(), m.group(0)))
    return hits


def _unpinned_pip(args: list[str]) -> bool:
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
        elif a in _PIP_VALUE_FLAGS:
            skip_next = True
        elif a.startswith("-") or a.startswith(("{{", ".", "/", "~", "git+")) or "/" in a:
            continue
        elif a.endswith((".whl", ".tar.gz", ".zip")) or any(c in a for c in "=<>~!@"):
            continue
        elif re.sub(r"\[.*\]$", "", a).lower() in _ALWAYS_UNPINNED_OK:
            continue
        elif re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?", a):
            return True
    return False


# --- Warnings and confirmations --------------------------------------------------

# A warning must govern the command itself: a negation directly before it, optionally
# with a verb like "run", and possibly other commands joined by "or"/"and". A negation
# elsewhere in the sentence does not count, so "don't hesitate to run `x`" or
# "don't forget to run `x`" are instructions, not warnings.
_GOVERNING_NEGATION = re.compile(
    r"(?:\bnever|\bdon['’]?t|\bdo\s+not|\bmust\s+not|\bmustn['’]?t|\bshould\s+not|\bshouldn['’]?t"
    r"|\bavoid|(?<!why\s)\bnot(?:\s+to)?|\bunder\s+no\s+circumstances(?:\s+should\s+you)?)\s+"
    r"(?:(?:run|running|use|using|execute|executing|type|typing|call|calling|invoke|invoking"
    r"|do|doing|try|trying)\s+)?"
    # Earlier commands in the list extend the negation only when the last separator before
    # the governed command is a conjunction, not a bare comma: "do not use `a` or `b`" is a
    # warning about both, but "do not use `--interactive`, `rm -rf ~/` is faster" is not. The
    # list is bounded ({0,40}) so the nested groups cannot backtrack quadratically on hostile
    # input (a long run of "`x`, ").
    r"(?:(?:`[^`]*`\s*(?:,|\bor\b|\band\b|\bnor\b)\s*){0,40}"
    r"`[^`]*`\s*,?\s*(?:\bor\b|\band\b|\bnor\b)\s+)?$", re.I)
# A warning label at the start of the sentence.
_WARNING_LABEL = re.compile(r"^\W*(?:warning|caution|danger)\b|^\W*⚠", re.I)
# Words that describe the danger of a command: a negation, or the harm itself. A warning
# label ("Warning:") only marks a warning when wording like this is in the sentence --
# otherwise "Warning: run `rm -rf ~`" or "Caution: enter `x`" is a plain instruction that
# happens to carry a label. A listed verb on its own ("using `x`", "run `x`") does not
# count, so a label that merely describes or instructs the command is read correctly.
# `without`, `cannot`/`can't` and `won't` were dropped: they appear in instructions as
# often as warnings ("run `x` without asking", "you can't continue until you run `x`"), so
# they turned labelled instructions into skipped warnings.
_WARNING_WORDS = re.compile(
    r"\b(?:never|don['’]?t|do\s+not|must\s+not|should\s+not"
    r"|avoid|irreversibl\w*|permanent\w*|unrecoverabl\w*|irrecoverabl\w*|undone"
    r"|no\s+undo|delete[sd]|deleting|erase[sd]?|erasing|destroy(?:s|ed|ing)?|overwrit\w*"
    r"|wipe[sd]|lose[s]?|losing|lost|gone|forever|data\s+loss)\b", re.I)
_SQUISH = re.compile(r"\s+")


def _sentence_span(unit: _Unit, line_index: int, col: int) -> tuple[str, int, int, int]:
    """The prose unit as one string, and the start, match position and end of the
    sentence containing column col of line line_index. Inline code is not split.

    The boundaries are found once per unit (_text) and looked up here, so a paragraph
    with many matches is not rescanned for each one (MET-42)."""
    text = _text(unit)
    position = text.offsets[line_index] + col
    # The last sentence end strictly before the match, and the first one after it.
    before = bisect_left(text.starts, position)
    after = bisect_right(text.ends, position)
    start = text.starts[before - 1] if before else 0
    end = text.ends[after] if after < len(text.ends) else len(text.joined)
    return text.joined, start, position, end


def _clearable(unit: _Unit, line_index: int, col: int, considered: int, budget: _Budget) -> bool:
    """Whether a prose match may still be weighed for clearing: fewer than
    MAX_CLEARED_PER_UNIT already weighed in this unit, a sentence no longer than
    MAX_CLEARED_SENTENCE, and the skill's MAX_CLEARING_CHARS not spent. Weighing reads the
    whole sentence, so these bound the scan; a match that is not weighed is reported."""
    if considered >= MAX_CLEARED_PER_UNIT:
        return False
    _, start, _, end = _sentence_span(unit, line_index, col)
    if end - start > MAX_CLEARED_SENTENCE or end - start > budget.clearing_left:
        return False
    budget.clearing_left -= end - start
    return True


def _sentence(unit: _Unit, line_index: int, col: int) -> str:
    joined, start, _, end = _sentence_span(unit, line_index, col)
    return joined[start:end].strip()


_EXAMPLE_LEAD = re.compile(r"(?:such\s+as|e\.g\.,?|for\s+example,?|for\s+instance,?|including)\s*$", re.I)
_EXAMPLE_MARK = re.compile(r"\betc\b|\be\.g\.|\bfor\s+example\b|\bsuch\s+as\b", re.I)
# Destructive rules whose match stops at the flags, so a trailing positional operand (a
# remote and branch, a ref) marks a concrete instruction rather than a bare example.
_BARE_RULES = {"force-push", "history-rewrite"}


def _has_operand(text: str, col: int) -> bool:
    """Whether the git command at `col` has a positional argument after its subcommand
    and flags: `git push --force origin main` does, bare `git push --force` does not."""
    rest = text[col:]
    tokens = rest[:_command_end(rest)].split()
    return any(not t.startswith("-") for t in tokens[2:])  # after "git <subcommand>"


def _is_example(unit: _Unit, line_index: int, hit: _Hit) -> bool:
    """A command named as an example in prose rather than given as an instruction:
    inside parentheses with "etc." or "e.g.", or right after "such as" / "for example".

    Only a bare destructive command (`rm -rf`, `git reset --hard`) with no concrete
    operand can be a prose example. A delete with a real target, a force-push to a
    remote and branch, or a reset to a ref is a concrete instruction, as is any
    download/credential rule (`curl ... | bash`, `echo $SECRET`) whose dangerous operand
    is part of the match: those are flagged wherever they appear.
    """
    if hit.rule == "recursive-delete":
        if any(t != "(unspecified)" for t, _ in hit.targets):
            return False
    elif hit.rule in _BARE_RULES:
        if _has_operand(unit.lines[line_index][1], hit.start):
            return False
    else:
        return False
    joined, start, position, end = _sentence_span(unit, line_index, hit.start)
    prefix = joined[start:position].rstrip().rstrip("`").rstrip()
    if _EXAMPLE_LEAD.search(prefix):
        return True
    opened = prefix.rfind("(")
    if opened == -1 or opened < prefix.rfind(")"):
        return False
    closed = joined.find(")", position)
    parenthetical = joined[start + opened:closed if closed != -1 else end]
    return bool(_EXAMPLE_MARK.search(parenthetical))


def _is_warning(unit: _Unit, line_index: int, col: int) -> bool:
    """A prose match that its own sentence warns against (checks.md, warning check)."""
    if unit.warning_block:
        return True
    joined, start, position, end = _sentence_span(unit, line_index, col)
    prefix = _SQUISH.sub(" ", joined[start:position])
    # A negation that governs the command, optionally through a run-verb, is a warning:
    # "never run `x`", "do not use `x`".
    if _GOVERNING_NEGATION.search(prefix.rstrip().rstrip("`").rstrip() + " "):
        return True
    # A warning label alone is not enough: "Warning: run `x`" / "Caution: enter `x`"
    # instruct the command. A label is a warning only when the sentence also describes the
    # danger -- a negation or harm words ("deletes", "permanently", "cannot be undone").
    # Code spans are masked first, so a harm word inside the command itself
    # (`rm -rf ~/lost+found`) does not turn the sentence into a warning.
    sentence = re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), joined[start:end])
    return bool(_WARNING_LABEL.search(prefix.lstrip()) and _WARNING_WORDS.search(sentence))


# An actual confirmation step, not just the word: asking the user, getting their
# approval, an interactive prompt, a dry run, or a backup. "Confirm the export
# finished" or "no need to confirm" do not count.
# "ask the user" only counts when it asks about the action: "ask the user before", "ask
# the user for confirmation". "ask the user for their favourite colour" is not a
# confirmation. "back up" needs an object, so "go back up one level" does not count.
# input() counts only when its prompt looks like a yes/no question, not input("project
# name:"). `choice` was dropped: the English word gave false confirmations.
_CONFIRM = re.compile(
    r"\bask(?:s|ing)?\s+(?:(?:the\s+user|the\s+human|them|you)\s+"
    r"(?:before\b|first\b|whether\b|if\s+they\b|to\s+confirm\b|to\s+proceed\b"
    r"|for\s+(?:confirmation|approval|permission)\b)"
    r"|for\s+(?:confirmation|approval|permission)\b)"
    r"|\b(?:confirm|check)\s+with\s+(?:the\s+)?(?:user|human)\b"
    r"|\b(?:get|obtain|request|wait\s+for)\s+(?:the\s+user['’]s\s+|explicit\s+|their\s+)?"
    r"(?:confirmation|approval|permission)\b"
    r"|\bare\s+you\s+sure\b|\bdry[- ]?run\b|--dry-run\b"
    r"|-WhatIf\b(?!:\$?(?:false|0)\b)"  # -WhatIf:$false and -Confirm:$false switch it off
    r"|\b(?:make|take|create|keep)\s+(?:a\s+)?back[\s-]?up\b"
    r"|\bback\s+(?:it|them|everything)\s+up\b"
    r"|\bback\s+up\s+(?:your|the|all|everything|it|them|files|data|my|our|first|before)\b"
    # `read -p`, `read -r -p` and `read -t 10 -p`: flags, some with a value, before the p.
    r"|\bread\s+(?:-[a-zA-Z]+\s+(?:(?:'[^']{0,20}'|\"[^\"]{0,20}\"|[^\s;&|<>'\"-][^\s;&|]{0,40})\s+)?){0,6}"
    r"-[a-zA-Z]*p\b"
    r"|\binput\s*\([^)]*(?:y/n|\byes\b|\bsure\b|\bconfirm\b|\bproceed\b|\bcontinue\b|\[y)"
    r"|\bconfirm\s*\(|\bRead-Host\b|-Confirm\b(?!:\$?(?:false|0)\b)|\bset\s+/p\b"
    r"|\bchoice\s+/[a-z]",
    re.I)
# A question put to whoever runs the code, which a `read` or an `input()` then answers:
# `echo "Delete? [y/N]"` followed by `read answer`. Neither is a confirmation alone.
# The lookahead leaves each later `echo` to be tried too; _question_end then checks that
# no `;`, `&` or `|` outside a string ends the command before the question.
_PRINTS_QUESTION = re.compile(
    r"\b(?:echo|printf|print|Write-Host|Write-Output)\b(?=([^\n]{0,80}?"
    r"(?:are\s+you\s+sure|[\[(]\s*y(?:es)?\s*/\s*n"
    r"|\b(?:sure|confirm|proceed|continue|delete|remove|wipe|overwrite|erase)\b[^\"'\n]{0,60}\?)))",
    re.I)
_ENDS_COMMAND = re.compile(r"(?<!>)[;&|]")  # not the `&` of a redirect such as `>&2`
# `read` as a command that waits for the user: not `while read`, not after a pipe, and
# not from a file. It may stand after `!`, `builtin` or an assignment such as `IFS=`.
# `input()` with no prompt of its own.
_READS_ANSWER = re.compile(
    r"(?:\A|;|&&|\|\||\{|(?<![\w.$-])(?:then|do|if|elif)(?=\s))\s*(?:!\s+)?"
    r"(?:(?:[A-Za-z_]\w{0,30}=(?:\$?'[^']{0,20}'|\"[^\"]{0,20}\"|[^\s;&|'\"<>]{0,40})"
    r"|builtin|command)\s+){0,3}"
    r"(?P<read>read)\b(?![^;&|\n]{0,200}<(?!\s*/dev/tty))"
    r"|(?<![\w.])(?P<input>input)\s*\(\s*\)")
# A confirmation phrase is ignored when the words just before it negate it.
_NEGATION_REACH = 64  # > longest negation word (8) + the 30 characters after it
_NEGATED_BEFORE = re.compile(
    r"\b(?:no|not|without|skip(?:ping)?|don['’]?t|never|needn['’]?t)\b[^.\n]{0,30}$", re.I)


# "We make a backup of the logs weekly" describes what happens elsewhere; it asks nothing
# of whoever runs the command.
_BACKUP = re.compile(r"(?:make|take|create|keep|back)\b", re.I)
_DESCRIBED_BEFORE = re.compile(r"\b(?:we|they|i)\s+(?:\w+ly\s+)?$", re.I)
_BATCH_COMMENT = re.compile(r"\s*(?:rem\b|::)", re.I)
_SLASH_COMMENTS = {".js", ".mjs", ".cjs", ".ts"}
# A JavaScript pattern, /.../, and what a `/` must follow to open one rather than divide.
_JS_PATTERN = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\[])+/")
_BEFORE_JS_PATTERN = re.compile(
    r"(?:\A|[(,=:\[!&|?{};*%<>~^]|(?<!\+)\+|(?<!-)-|\b(?:return|typeof|case))\s*\Z")
# What can follow a pattern: its flags, then an operator or the end. After a `)` it also
# has to be used at once, as in `if (x) /'/.test(s)`, or it is a division.
_AFTER_JS_PATTERN = re.compile(r"[dgimsuvy]{0,8}\s*(?:[.,;:)\]}?|&=!+\-*%<>^]|\Z)")
_USED_JS_PATTERN = re.compile(r"[dgimsuvy]{0,8}\.(?:test|exec)\b")
# Languages in which a backslash escapes inside single quotes too, which a shell's do not.
_SINGLE_QUOTE_ESCAPES = _SLASH_COMMENTS | {".py"}


@lru_cache(maxsize=4)
def _quoted_spans(path: str, line: str) -> tuple[tuple[int, int, str], ...]:
    """(start, end, kind) parts of a line of code, read on its own: kind is "string",
    "comment" or "template", a JavaScript backtick string. A template literal can run over
    lines, so a backtick may as well close one as open it: what is in a "template" is not
    taken to be text that never runs."""
    suffix = Path(path).suffix.lower()
    if suffix in (".bat", ".cmd") and _BATCH_COMMENT.match(line):
        return ((0, len(line), "comment"),)
    # PowerShell and cmd.exe do not escape with a backslash: "C:\dir\" ends at the quote.
    escapes = suffix not in (".ps1", ".psm1", ".bat", ".cmd")
    # A block comment opened and, if it is, closed on this line.
    block = (("<#", "#>") if suffix in (".ps1", ".psm1")
             else ("/*", "*/") if suffix in _SLASH_COMMENTS else None)
    spans, quote, opened, i, ansi = [], "", 0, 0, False
    patterns = suffix in _SLASH_COMMENTS
    tried = 0  # no pattern is looked for inside the last candidate (MET-42)
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and escapes and (quote != "'" or ansi
                                           or suffix in _SINGLE_QUOTE_ESCAPES):
                i += 1
            elif c == quote:
                spans.append((opened, i + 1, "template" if quote == "`" else "string"))
                quote = ""
        elif c == "\\" and escapes:
            i += 1  # `don\'t` opens no string
        elif c in "'\"" or (c == "`" and suffix in _SLASH_COMMENTS
                           and line.find("`", i + 1) >= 0):  # alone, it opens nothing here
            quote, opened = c, i
            ansi = c == "'" and line[i - 1:i] == "$"  # bash's $'it\\'s'
        elif (block and line.startswith(block[0], i)
                and (i == 0 or line[i - 1] in " \t;,(){=")):  # not in `~/*`, `${a}/*`
            end = line.find(block[1], i + 2)
            end = len(line) if end < 0 else end + 2
            spans.append((i, end, "comment"))
            i = end
            continue
        elif (i == 0 or line[i - 1].isspace()) and (
                c == "#" or (line.startswith("//", i) and (suffix in _SLASH_COMMENTS
                                                          or suffix in MARKDOWN_SUFFIXES))):
            spans.append((i, len(line), "comment"))
            break
        elif c == "/" and patterns and i >= tried and not line.startswith(("//", "/*"), i):
            # A quote in a pattern such as /`/g or /["']/ opens no string.
            before = _BEFORE_JS_PATTERN.search(line, max(0, i - 20), i)
            if before or line[max(0, i - 20):i].rstrip().endswith(")"):
                found = _JS_PATTERN.match(line, i)
                if not found:
                    patterns = False  # none closes further on: do not look again (MET-42)
                else:
                    tried = found.end() - 1  # its closing slash may open the next
                    follows = _AFTER_JS_PATTERN if before else _USED_JS_PATTERN
                    if follows.match(line, found.end()):
                        i = found.end()
                        continue
        i += 1
    if quote:
        spans.append((opened, len(line), "string"))
    return tuple(spans)


def _span_at(path: str, line: str, col: int) -> tuple[int, int, str] | None:
    """The string or comment column col of a line of code is in, if one."""
    spans = _quoted_spans(path, line)
    # Spans do not overlap and are in order: bisect, as a line can hold thousands.
    i = bisect_right(spans, col, key=lambda span: span[0]) - 1
    if i < 0:
        return None
    start, end, kind = spans[i]
    # Strictly inside: a match that begins at a quote or a # is the command itself.
    if start < col < end or (kind == "comment" and col == start):
        return spans[i]
    return None


def _confirmations(unit: _Unit, line_index: int) -> int | None:
    """The column of the first confirmation step on a line, or None. Cached per line.

    Not counted: a negated phrase, a backup that is only described, and in code a phrase
    inside a comment or a string, which asks nothing of anyone."""
    key = ("confirmations", line_index)
    if key not in unit.cache:
        line = unit.lines[line_index][1]
        found = None
        spans = None if unit.kind == "prose" else _quoted_spans(unit.path, line)
        at = 0
        for m in _CONFIRM.finditer(line):
            # The patterns before a phrase span at most about 40 characters, so they are
            # searched only just before it, not on the whole line up to it (MET-42). The
            # search starts mid-line with pos, so \b still sees the character before it.
            since = max(0, m.start() - _NEGATION_REACH)
            if _NEGATED_BEFORE.search(line, since, m.start()):
                continue
            if _BACKUP.match(m.group(0)) and _DESCRIBED_BEFORE.search(line, since, m.start()):
                continue
            if spans:
                while at < len(spans) and spans[at][1] <= m.start():
                    at += 1
                if at < len(spans) and spans[at][0] <= m.start():
                    continue
            found = m.start()
            break
        if unit.kind != "prose" and (answer := _answer(unit, line_index)) is not None:
            found = answer if found is None else min(found, answer)
        unit.cache[key] = found
    return unit.cache[key]


def _question_end(unit: _Unit, line_index: int) -> int | None:
    """Where the first printed question on a line of code ends, if it prints one. Cached."""
    key = ("question", line_index)
    if key not in unit.cache:
        line = unit.lines[line_index][1]
        unit.cache[key] = next(
            (m.end(1) for m in _PRINTS_QUESTION.finditer(line)
             if _span_at(unit.path, line, m.start()) is None
             and all(_span_at(unit.path, line, end.start()) is not None
                     for end in _ENDS_COMMAND.finditer(line, m.end(), m.end(1)))), None)
    return unit.cache[key]


def _answer(unit: _Unit, line_index: int) -> int | None:
    """The column of the first `read` or `input()` on a line of code that answers a
    question printed before it: earlier on the line, or in the lines just before."""
    line = unit.lines[line_index][1]
    asked = any(_question_end(unit, i) is not None
                for i in range(max(0, line_index - CONFIRM_LOOKBACK), line_index))
    here = _question_end(unit, line_index)
    for m in _READS_ANSWER.finditer(line):
        col = m.start("read" if m["read"] else "input")
        if (asked or here is not None and here <= col) and _span_at(unit.path, line, col) is None:
            return col
    return None


def _confirmed(unit: _Unit, line_index: int, col: int, previous: _Unit | None) -> bool:
    """A confirmation step before a destructive command (checks.md, confirmation check):
    earlier on its line, on a line before it (the five before it in a script, the whole
    unit elsewhere), or in the paragraph before. One that comes after does not count.

    What each line and each unit holds is found once, so many matches stay linear (MET-42)."""
    own = _confirmations(unit, line_index)
    if own is not None and own < col:
        return True
    if unit.kind == "script":
        return any(_confirmations(unit, i) is not None
                   for i in range(max(0, line_index - CONFIRM_LOOKBACK), line_index))
    if "first_confirmation" not in unit.cache:
        unit.cache["first_confirmation"] = next(
            (i for i in range(len(unit.lines)) if _confirmations(unit, i) is not None),
            len(unit.lines))
    if unit.cache["first_confirmation"] < line_index:
        return True
    if previous is None or previous.kind != "prose":
        return False
    if "confirms" not in previous.cache:
        previous.cache["confirms"] = any(
            _confirmations(previous, i) is not None for i in range(len(previous.lines)))
    return previous.cache["confirms"]


# --- Delete targets --------------------------------------------------------------

# Any assignment counts; only a plain literal value can be followed.
# Leading whitespace is horizontal only ([^\S\n]): with re.M, `^\s*` let every line start
# re-scan a run of blank lines below it, quadratic in the run (MET-42). A name starts its
# own line either way, so the matches are the same.
_SHELL_ASSIGN = re.compile(r"^[^\S\n]*(?:export\s+|local\s+|readonly\s+|declare\s+)?([A-Za-z_]\w*)=(\S*)", re.M)
_SHELL_LITERAL = re.compile(r"\"([^\"$`]*)\"|'([^']*)'|([^\s;&|\"'$`]*)")
# The value runs to the end of the line; _assignments cuts a `#` comment and trailing
# whitespace. A lazy `(.*?)\s*(?:#.*)?$` did the same but backtracked across a long run of
# spaces once per character, quadratic in the run (MET-42).
_PY_ASSIGN = re.compile(r"^[^\S\n]*([A-Za-z_]\w*)\s*=(?!=)\s*(.*)$", re.M)
_CMD_LITERAL = re.compile(r"([^%!]*)")
_PY_LITERAL = re.compile(r"(['\"])([^'\"]*)\1")
_PY_ANY = re.compile(r"(.+)")  # Python values are resolved by _python_path
_CMD_ASSIGN = re.compile(r"^[^\S\n]*set\s+\"?([A-Za-z_]\w*)=([^\"&|\r\n]*)\"?\s*$", re.M | re.I)
_PS_ASSIGN = re.compile(r"^[^\S\n]*\$(?:env:)?([A-Za-z_]\w*)\s*=\s*(.*)$", re.M)
# $VAR, ${VAR}, ${VAR:?msg}, and on Windows %VAR%, $env:VAR, ${env:VAR}
_VAR_REF = re.compile(r"\$\{(?:env:)?([A-Za-z_]\w*)(?::\?[^}]*)?\}|\$(?:env:)?([A-Za-z_]\w*)|%([A-Za-z_]\w*)%")
_LN = re.compile(r"(?<![\w.-])ln\s+")


def _assignments(pattern: re.Pattern, literal: re.Pattern, text: str,
                 comments: bool = False) -> dict[str, list[str | None]]:
    """Every assignment per name; None for a value that is not a plain literal.
    comments: the value ends at the first `#`, and trailing whitespace is dropped."""
    found: dict[str, list[str | None]] = {}
    for m in pattern.finditer(text):
        name, value = m.group(1), m.group(2)
        if comments:
            cut = value.find("#")
            value = (value if cut == -1 else value[:cut]).rstrip()
        value = value.rstrip(";")
        lit = literal.fullmatch(value)
        found.setdefault(name, []).append(
            next((g for g in lit.groups() if g is not None and g not in ("'", '"')), "") if lit else None)
    return found


def _created_links(units: list[_Unit]) -> set[str]:
    """Names the skill turns into symlinks with ln -s."""
    names = set()
    for unit in units:
        for lineno, text in unit.lines:
            _parsing_at(unit.path, lineno)
            for m in _LN.finditer(text):
                args = _args(text, m.end())
                if not any(a == "--symbolic" or (a.startswith("-") and not a.startswith("--") and "s" in a)
                           for a in args):
                    continue
                operands = [a for a in args if not a.startswith("-")]
                if operands:
                    link = operands[-1] if len(operands) > 1 else Path(operands[0]).name
                    names.add(_normalize(link))
    return names


def _bundle_symlinks(skill: Skill) -> list[tuple[str, str]]:
    """Symlinks inside the skill's folder that point outside it, as (path, link text)."""
    root = Path(skill.path)
    if not root.is_dir():
        return []
    real_root = os.path.realpath(root)
    found = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink():
                resolved = os.path.realpath(path)
                try:
                    inside = os.path.commonpath([real_root, resolved]) == real_root
                except ValueError:  # different drives on Windows
                    inside = False
                if not inside:
                    found.append((path.relative_to(root).as_posix(), link_text(path)))
    return found


def without_extended_prefix(target: str) -> str:
    """A Windows link target as people write it. os.readlink returns an absolute target
    with the extended-length prefix (`\\\\?\\C:\\x`, `\\\\?\\UNC\\server\\share`), which
    realpath drops, so a message would otherwise show one path two ways."""
    if target.startswith("\\\\?\\UNC\\"):
        return "\\\\" + target[len("\\\\?\\UNC\\"):]
    if target.startswith("\\\\?\\"):
        return target[len("\\\\?\\"):]
    return target


def link_text(path: Path | str) -> str:
    """What the link at `path` points to, as written, for a message. On Windows without the
    extended-length prefix; elsewhere exactly as written, as `\\\\?\\` is an ordinary name there."""
    target = os.readlink(path)
    return without_extended_prefix(target) if os.name == "nt" else target


def _climbs_out(relative: str) -> bool:
    """Whether the `..` in a relative path lead above where it starts."""
    depth = 0
    for part in relative.split("/"):
        depth += -1 if part == ".." else part not in ("", ".")
        if depth < 0:
            return True
    return False


def _normalize(target: str) -> str:
    t = target.strip().strip("'\"").replace("\\", "/")
    # `~/./Documents` and `~//Documents` name the same folder as `~/Documents`.
    t = re.sub(r"/(?:\.(?=/|$))+", "", re.sub(r"/{2,}", "/", t)) or ("/" if t else "")
    # A path from a fixed start also has its `..` resolved. A trailing `..` is kept (it is
    # judged as written, CRITICAL), and so is any path whose start is not known.
    if t.startswith(("/", "~/", "$HOME/", "${HOME}/")) and "/../" in t and not t.endswith("/.."):
        head, rest = (t.split("/", 1) if not t.startswith("/") else ("", t[1:]))
        if "$" not in rest:
            if head and _climbs_out(rest):
                t = head + "/.."  # above the home folder: no safer than naming its parent
            else:
                resolved = posixpath.normpath("/" + rest)
                t = head + resolved if head else resolved
    while t.endswith(("/*", "/.")) and len(t) > 2:
        t = t[:-2]
    while t.endswith("/") and len(t) > 1:
        t = t[:-1]
    while t.startswith("./") and len(t) > 2:
        t = t[2:]
    return t


def _substitute(target: str, values: dict[str, list[str | None]]) -> str:
    """Replace $VAR with its value when the file assigns it exactly once, to a literal."""
    def replace(m: re.Match) -> str:
        name = m.group(1) or m.group(2) or m.group(3)
        assigned = values.get(name, [])
        return assigned[0] if len(assigned) == 1 and assigned[0] is not None else m.group(0)
    return _VAR_REF.sub(replace, target)


def _classify(raw: str, follows: bool, path: str, py_var: bool, ctx: _Context) -> str:
    if py_var:
        assigned = ctx.py_vars.get(path, {}).get(raw, [])
        if len(assigned) != 1 or assigned[0] is None:
            return "unknown"
        raw = assigned[0]
    t = _canonical(_substitute(raw.strip().strip("'\""), ctx.shell_vars.get(path, {})))
    norm = _normalize(t)
    if "{{" in t:
        return "unknown"
    if norm in ctx.links and follows:
        return "symlink"  # a delete that goes through the link; without following, only the link goes
    if _is_system(norm):
        return "system"
    var = re.match(r"\$\{?([A-Za-z_]\w*)(:\?)?", t)
    always = var is not None and (var.group(1) in ALWAYS_SET or var.group(1).upper() in WINDOWS_ALWAYS_SET)
    if var and not var.group(2) and not always and t[var.end():].lstrip("}").startswith("/"):
        return "empty-variable"
    if _is_rebuildable(norm):
        return "rebuildable"
    return "unknown"


def _canonical(target: str) -> str:
    """Windows forms in the forward-slash form the rules use.

    `C:\\Windows` becomes `c:/windows`, `%USERPROFILE%` and `$env:USERPROFILE`
    become `$HOME`, `%TEMP%` becomes `/tmp`, and other `%VAR%` / `$env:VAR`
    become `$VAR`, so they are judged like Unix variables.
    """
    t = target.replace("\\", "/")

    def var(m: re.Match) -> str:
        name = m.group(1) or m.group(2) or m.group(3)
        if name.upper() in WINDOWS_HOME_VARS:
            return "$HOME"
        if name.upper() in WINDOWS_TEMP_VARS:
            return "/tmp"
        return m.group(0) if m.group(0).startswith("$") and "env:" not in m.group(0) else f"${name}"

    t = _VAR_REF.sub(var, t)
    if re.match(r"^[A-Za-z]:(?:/|$)", t):
        t = t.lower()
    return t


def is_system_path(path: str) -> bool:
    """True for a system or home folder, written in Unix or Windows form."""
    return _is_system(_normalize(_canonical(path)))


def _is_system(norm: str) -> bool:
    if norm in SYSTEM_EXACT or norm.startswith(SYSTEM_PREFIXES):
        return True
    drive = re.match(r"^[a-z]:(?:/(.*))?$", norm)
    if drive:
        rest = drive.group(1) or ""
        personal = "|".join(HOME_PERSONAL)
        return (rest == "" or rest in WINDOWS_SYSTEM_EXACT or rest.startswith(WINDOWS_SYSTEM_PREFIXES)
                or re.fullmatch(rf"users/[^/]+(?:/(?:{personal}|appdata))?", rest) is not None)
    if re.fullmatch(r"/(?:home|Users)/[^/]+", norm):
        return True
    if norm in HOME_FORMS or any(norm.lower() == f"{h}/{d}".lower()
                                 for h in HOME_FORMS for d in HOME_CRITICAL + HOME_PERSONAL):
        return True
    personal = "|".join(HOME_PERSONAL)
    if re.fullmatch(rf"(?:/home|/users|[a-z]:/users)/[^/]+/(?:{personal})", norm, re.I):
        return True
    return norm in ("..", ".git") or norm.endswith(("/..", "/.git"))


def _is_rebuildable(norm: str) -> bool:
    if any(c in norm for c in "*?[$`{") or ".." in norm.split("/"):
        return False
    if norm.startswith("/tmp/") and len(norm) > len("/tmp/"):
        return True
    if norm.startswith(("/", "~")):
        return False
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    return bool(parts) and (parts[-1] in REBUILDABLE or parts[-1].endswith(".egg-info"))


def _finding(hit: _Hit, unit: _Unit, lineno: int, text: str, ctx: _Context,
             targets: list[DeleteTarget]) -> Finding:
    location = f"{unit.path}:{lineno}"
    if hit.rule != "recursive-delete":
        return Finding(Dimension.SAFETY, hit.category, hit.severity, ChecksBackend.name, hit.rule,
                       _stripped(text), location, MESSAGES[hit.rule])

    judged = []
    for raw, follows in hit.targets:
        kind = _classify(raw, follows, unit.path, hit.py_var, ctx)
        target = DeleteTarget(raw, location, _stripped(text), follows, kind, _KIND_SEVERITY[kind])
        targets.append(target)
        judged.append(target)
    worst = max(judged, key=lambda t: SEVERITY_RANK[t.severity])
    return Finding(
        Dimension.SAFETY, DESTRUCTIVE, worst.severity, ChecksBackend.name, hit.rule, _stripped(text),
        location,
        f"Recursive delete of {_KIND_TEXT[worst.kind]} (`{worst.target}`) with no confirmation step")
