"""Own deterministic checks for the careless side of safety (docs/safety/checks.md).

Rules for destructive-unconfirmed, credential-logging and untrusted-source, plus
symlinks that point outside the skill and characters a person cannot see. No model and no network: the same skill
always gives the same findings.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from metaskill.evaluate.judge import mark_hidden
from metaskill.evaluate.models import Dimension, Finding, SafetyTier, Severity, Skill
from metaskill.evaluate.safety.findings import SEVERITY_RANK, BackendResult, BackendStatus

# Bumped whenever a rule is added or changed; recorded as checks_version.
CHECKS_VERSION = "2"

MARKDOWN_SUFFIXES = {".md", ".markdown"}
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".psm1",
                   ".bat", ".cmd"}
MAX_FILE_BYTES = 1_000_000
CONFIRM_LOOKBACK = 5  # script lines before a command searched for a confirmation
MAX_LISTED_SKIPS = 10

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
}

# --- Delete targets (checks.md, "Delete targets") ------------------------------

REBUILDABLE = {
    "build", "dist", "out", "target", "node_modules", "__pycache__", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "htmlcov",
}
HOME_FORMS = ("~", "$HOME", "${HOME}")
HOME_CRITICAL = (".ssh", ".config", ".gnupg", "appdata")  # compared case-insensitively
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
    """A prose match skipped because its sentence warns against it, for the deep
    review's re-check (docs/safety/review.md, "Re-checking skipped warnings")."""

    location: str  # e.g. "SKILL.md:12"
    rule: str
    snippet: str  # the matched command, e.g. "rm -rf ~/"
    sentence: str  # the sentence containing the match
    paragraph: str  # the paragraph or list item it is in
    finding: Finding  # what it becomes if the re-check says it is an instruction


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
    """Prose matches skipped as warnings that would otherwise be findings. Same
    parsing as scan(); a match that was also confirmed is left out, since it would
    not be a finding either way."""
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


@dataclass
class _Context:
    """Per-skill facts used to judge delete targets."""

    # path -> name -> every assigned value (None when not a plain literal)
    shell_vars: dict[str, dict[str, list[str | None]]] = field(default_factory=dict)
    py_vars: dict[str, dict[str, list[str | None]]] = field(default_factory=dict)
    links: set[str] = field(default_factory=set)  # names that are or become symlinks


def _run(skill: Skill) -> tuple[list[Finding], list[DeleteTarget], list[str], list[SkippedWarning]]:
    notes: list[str] = []
    files = _read_files(skill, notes)
    units = [u for path, text in files for u in _units(path, text)]

    ctx = _Context()
    for path, text in files:
        ctx.shell_vars[path] = _assignments(_SHELL_ASSIGN, _SHELL_LITERAL, text)
        if Path(path).suffix.lower() in (".bat", ".cmd"):
            ctx.shell_vars[path] = _assignments(_CMD_ASSIGN, _CMD_LITERAL, text)
        elif Path(path).suffix.lower() in (".ps1", ".psm1"):
            ctx.shell_vars[path] = _assignments(_PS_ASSIGN, _PY_LITERAL, text)
        ctx.py_vars[path] = _assignments(_PY_ASSIGN, _PY_LITERAL, text)
    bundle_links = _bundle_symlinks(skill)
    ctx.links = {_normalize(name) for name, _ in bundle_links} | _created_links(units)

    found: dict[tuple[str, int, str], Finding] = {}
    targets: list[DeleteTarget] = []
    skipped: list[str] = []
    rechecks: list[SkippedWarning] = []

    for u_index, unit in enumerate(units):
        previous = units[u_index - 1] if u_index and units[u_index - 1].path == unit.path else None
        for l_index, (lineno, text) in enumerate(unit.lines):
            for hit in _hits(text, unit):
                location = f"{unit.path}:{lineno}"
                if unit.kind == "prose" and _is_warning(unit, l_index, hit.start):
                    skipped.append(f"{location} ({hit.snippet})")
                    if not (hit.category == DESTRUCTIVE and (
                            hit.confirmed or _confirmed(unit, l_index, previous))):
                        rechecks.append(SkippedWarning(
                            location, hit.rule, hit.snippet,
                            _sentence(unit, l_index, hit.start),
                            " ".join(t.strip() for _, t in unit.lines),
                            # A throwaway target list: skipped deletes are not delete targets.
                            _finding(hit, unit, lineno, text, ctx, [])))
                    continue
                if hit.category == DESTRUCTIVE and (
                        hit.confirmed or _confirmed(unit, l_index, previous)):
                    continue
                finding = _finding(hit, unit, lineno, text, ctx, targets)
                key = (unit.path, lineno, finding.rule)
                if key not in found or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[found[key].severity]:
                    found[key] = finding
        if unit.kind in ("code", "script"):
            for f in _shell_trace(unit):
                found.setdefault((unit.path, int(f.location.rsplit(":", 1)[1]), f.rule), f)

    for path, text in files:
        for f in _hidden_characters(path, text):
            found.setdefault((path, int(f.location.rsplit(":", 1)[1]), f.rule), f)

    for name, pointing_to in bundle_links:
        found[(name, 0, "bundle-symlink")] = Finding(
            Dimension.SAFETY, EXCESS_SCOPE, Severity.MEDIUM, ChecksBackend.name,
            "bundle-symlink", f"{name} -> {pointing_to}", name, MESSAGES["bundle-symlink"])

    if skipped:
        shown = ", ".join(skipped[:MAX_LISTED_SKIPS])
        more = len(skipped) - MAX_LISTED_SKIPS
        notes.insert(0, f"skipped as a warning: {shown}" + (f" and {more} more" if more > 0 else ""))
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


def _read_files(skill: Skill, notes: list[str]) -> list[tuple[str, str]]:
    """(relative path, text) for SKILL.md and every markdown or script file.

    Files that are symlinks are not read, so a link cannot make the check read
    something outside the skill.
    """
    files = [("SKILL.md", skill.body)]
    for rel in skill.files:
        rel = Path(rel)
        if rel.suffix.lower() not in MARKDOWN_SUFFIXES | SCRIPT_SUFFIXES:
            continue
        path = Path(skill.path) / rel
        try:
            if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            files.append((rel.as_posix(), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            notes.append(f"could not read {rel.as_posix()}: {type(e).__name__}")
    return files


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_WARNING_BLOCK = re.compile(
    r"^\s*>\s*(?:\[!(?:WARNING|CAUTION|DANGER)\]|\*\*(?:warning|caution|danger)\b"
    r"|(?:warning|caution|danger)\s*:|⚠)", re.I)


def _units(path: str, text: str) -> list[_Unit]:
    if Path(path).suffix.lower() in SCRIPT_SUFFIXES:
        return [_Unit(path, "script", list(enumerate(text.splitlines(), 1)))]

    units: list[_Unit] = []
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":  # skip YAML frontmatter
        start = next((i + 1 for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)

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


def _command_end(rest: str) -> int:
    """Where a command ends: ; & | ` ) a newline or a comment, outside quotes."""
    quote = ""
    for i, c in enumerate(rest):
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in ";&|`)\n" or (c == "#" and i > 0 and rest[i - 1].isspace()):
            return i
    return len(rest)


def _args(text: str, end: int) -> list[str]:
    """Shell-like arguments after position end, up to the end of the command."""
    rest = text[end:]
    segment = rest[:_command_end(rest)]
    try:
        # Backslashes are path separators on Windows, not escapes: split without
        # POSIX rules there, and quotes are stripped when a target is judged.
        tokens = shlex.split(segment, posix="\\" not in segment)
    except ValueError:
        tokens = segment.split()
    return [t for t in tokens if "<" not in t and ">" not in t]


def _in_inline_code(text: str, col: int) -> bool:
    return text[:col].count("`") % 2 == 1


def _hits(text: str, unit: _Unit) -> list[_Hit]:
    hits: list[_Hit] = []
    prose = unit.kind == "prose"
    hits += _deletes(text, prose)
    hits += _other_destructive(text)
    hits += _credential_hits(text, prose)
    hits += _untrusted_hits(text)
    return hits


_RM = re.compile(r"(?<![\w.-])rm\s+")
_RMTREE = re.compile(r"shutil\.rmtree\(\s*(?:(['\"])(.*?)\1|([A-Za-z_][\w.]*))")
_REMOVE_ITEM = re.compile(r"(?<![\w-])Remove-Item\b", re.I)
_FIND = re.compile(r"(?<![\w.-])find\s+")
_CMD_DELETE = re.compile(r"(?<![\w.%$-])(?:rmdir|rd|del|erase)(?=\s)", re.I)


def _follows(target: str) -> bool:
    return target.endswith("/") or bool(re.search(r"/[^/]*[*?\[]", target))


def _deletes(text: str, prose: bool) -> list[_Hit]:
    hits = []
    for m in _RM.finditer(text):
        args = _args(text, m.end())
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
            if not prose:
                continue
            targets = ["(unspecified)"]  # "run `rm -rf` on the build folder": target not parseable
        if prose and not _in_inline_code(text, m.start()):
            targets = targets[:1]  # in plain prose only the next word can be the target
        interactive = any(f == "--interactive" or (not f.startswith("--") and re.search("[iI]", f))
                          for f in flags)
        hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                         f"rm {' '.join(flags)} {targets[0]}", interactive,
                         [(t, _follows(t)) for t in targets]))

    for m in _RMTREE.finditer(text):
        literal, variable = m.group(2), m.group(3)
        hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(), m.group(0) + ")",
                         targets=[(literal if literal is not None else variable, False)],
                         py_var=literal is None))

    for m in _REMOVE_ITEM.finditer(text):
        args = _args(text, m.end())
        if not any(a.lower() == "-recurse" for a in args):
            continue
        targets = [a for a in args if not a.startswith("-")]
        if targets:
            hits.append(_Hit("recursive-delete", DESTRUCTIVE, None, m.start(),
                             f"Remove-Item {targets[0]} -Recurse",
                             any(a.lower() in ("-confirm", "-whatif") for a in args),
                             [(t, True) for t in targets]))

    for m in _CMD_DELETE.finditer(text):
        args = _args(text, m.end())
        if not any(a.lower() == "/s" for a in args):
            continue  # not recursive
        targets = [a for a in args if not a.startswith("/")]
        if targets:
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
            hits.append(_Hit("echo-secret", CREDENTIALS, Severity.HIGH, m.start(), text[m.start():].strip()[:60]))

    for m in _PRINT.finditer(text):
        args = text[m.end():]
        names = [a or b for a, b in _ENV_NAMES.findall(args)]
        for inner in re.findall(r"\{([^{}]*)\}", args):  # f-strings and template literals
            names += re.findall(r"[A-Za-z_]\w*", inner)
        names += re.findall(r"[A-Za-z_]\w*", _STRING.sub(" ", args))
        if any(is_secret_name(n) for n in names):
            hits.append(_Hit("print-secret", CREDENTIALS, Severity.HIGH, m.start(), text[m.start():].strip()[:60]))

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
    r"(?<![\w-])(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|python3?|perl|ruby|node)\b"
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

_WARNING_WORDS = re.compile(
    r"\b(?:never|don['’]?t|do\s+not|must\s+not|mustn['’]?t|should\s+not|shouldn['’]?t|avoid|not"
    r"|warning|caution|danger\w*)\b|important:|⚠", re.I)
_NOT_A_WARNING = re.compile(
    r"\b(?:don['’]?t|do\s+not)\s+(?:forget|skip)\b|\bnever\s+mind\b|\bavoid\b.*\bby\s+(?:running|using)\b",
    re.I)


def _sentence_span(unit: _Unit, line_index: int, col: int) -> tuple[str, int, int, int]:
    """The prose unit as one string, and the start, match position and end of the
    sentence containing column col of line line_index. Inline code is not split."""
    texts = [text for _, text in unit.lines]
    joined = " ".join(texts)
    position = sum(len(t) + 1 for t in texts[:line_index]) + col
    masked = re.sub(r"`[^`]*`", lambda m: "x" * len(m.group(0)), joined)
    starts = [m.end() for m in re.finditer(r"[.!?](?=\s)", masked[:position])]
    end = re.compile(r"[.!?](?=\s|$)").search(masked, position)
    return joined, starts[-1] if starts else 0, position, end.end() if end else len(joined)


def _sentence(unit: _Unit, line_index: int, col: int) -> str:
    joined, start, _, end = _sentence_span(unit, line_index, col)
    return joined[start:end].strip()


def _is_warning(unit: _Unit, line_index: int, col: int) -> bool:
    """A prose match that its own sentence warns against (checks.md, warning check)."""
    if unit.warning_block:
        return True
    joined, start, position, _ = _sentence_span(unit, line_index, col)
    prefix = joined[start:position]
    return bool(_WARNING_WORDS.search(prefix)) and not _NOT_A_WARNING.search(prefix)


_CONFIRM = re.compile(
    r"\b(?:ask(?:s|ing)?|confirm\w*|are\s+you\s+sure|dry[- ]?run|back(?:[\s-]?up)s?)\b"
    r"|\bread\s+-[a-zA-Z]*p\b|\binput\s*\(|\bconfirm\s*\(|--dry-run\b|\bRead-Host\b|-WhatIf\b"
    r"|-Confirm\b|\bset\s+/p\b|\bchoice\b",
    re.I)


def _confirmed(unit: _Unit, line_index: int, previous: _Unit | None) -> bool:
    """A confirmation step near a destructive command (checks.md, confirmation check)."""
    if unit.kind == "script":
        window = unit.lines[max(0, line_index - CONFIRM_LOOKBACK):line_index + 1]
    else:
        window = list(unit.lines)
        if previous is not None and previous.kind == "prose":
            window += previous.lines
    return any(_CONFIRM.search(text) for _, text in window)


# --- Delete targets --------------------------------------------------------------

# Any assignment counts; only a plain literal value can be followed.
_SHELL_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+|declare\s+)?([A-Za-z_]\w*)=(\S*)", re.M)
_SHELL_LITERAL = re.compile(r"\"([^\"$`]*)\"|'([^']*)'|([^\s;&|\"'$`]*)")
_PY_ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*=(?!=)\s*(.*?)\s*(?:#.*)?$", re.M)
_CMD_LITERAL = re.compile(r"([^%!]*)")
_PY_LITERAL = re.compile(r"(['\"])([^'\"]*)\1")
_CMD_ASSIGN = re.compile(r"^\s*set\s+\"?([A-Za-z_]\w*)=([^\"&|\r\n]*)\"?\s*$", re.M | re.I)
_PS_ASSIGN = re.compile(r"^\s*\$(?:env:)?([A-Za-z_]\w*)\s*=\s*(.*?)\s*(?:#.*)?$", re.M)
# $VAR, ${VAR}, ${VAR:?msg}, and on Windows %VAR%, $env:VAR, ${env:VAR}
_VAR_REF = re.compile(r"\$\{(?:env:)?([A-Za-z_]\w*)(?::\?[^}]*)?\}|\$(?:env:)?([A-Za-z_]\w*)|%([A-Za-z_]\w*)%")
_LN = re.compile(r"(?<![\w.-])ln\s+")


def _assignments(pattern: re.Pattern, literal: re.Pattern, text: str) -> dict[str, list[str | None]]:
    """Every assignment per name; None for a value that is not a plain literal."""
    found: dict[str, list[str | None]] = {}
    for m in pattern.finditer(text):
        name, value = m.group(1), m.group(2).rstrip(";")
        lit = literal.fullmatch(value)
        found.setdefault(name, []).append(
            next((g for g in lit.groups() if g is not None and g not in ("'", '"')), "") if lit else None)
    return found


def _created_links(units: list[_Unit]) -> set[str]:
    """Names the skill turns into symlinks with ln -s."""
    names = set()
    for unit in units:
        for _, text in unit.lines:
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
                    found.append((path.relative_to(root).as_posix(), os.readlink(path)))
    return found


def _normalize(target: str) -> str:
    t = target.strip().strip("'\"").replace("\\", "/")
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
        return (rest == "" or rest in WINDOWS_SYSTEM_EXACT or rest.startswith(WINDOWS_SYSTEM_PREFIXES)
                or re.fullmatch(r"users/[^/]+", rest) is not None)
    if re.fullmatch(r"/(?:home|Users)/[^/]+", norm):
        return True
    if norm in HOME_FORMS or any(norm.lower() == f"{h}/{d}".lower() for h in HOME_FORMS for d in HOME_CRITICAL):
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
                       text.strip(), location, MESSAGES[hit.rule])

    judged = []
    for raw, follows in hit.targets:
        kind = _classify(raw, follows, unit.path, hit.py_var, ctx)
        target = DeleteTarget(raw, location, text.strip(), follows, kind, _KIND_SEVERITY[kind])
        targets.append(target)
        judged.append(target)
    worst = max(judged, key=lambda t: SEVERITY_RANK[t.severity])
    return Finding(
        Dimension.SAFETY, DESTRUCTIVE, worst.severity, ChecksBackend.name, hit.rule, text.strip(),
        location,
        f"Recursive delete of {_KIND_TEXT[worst.kind]} (`{worst.target}`) with no confirmation step")
