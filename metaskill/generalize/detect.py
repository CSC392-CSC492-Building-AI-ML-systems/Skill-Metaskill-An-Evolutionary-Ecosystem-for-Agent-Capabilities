"""Deterministic detection of personal and configuration values (design.md, section 3.2 step 2).

Each detector reads one SourceFile and yields Candidates. detect_file() runs the detectors
in priority order and keeps a candidate only if no earlier one already covers part of its
span, so `GITHUB_TOKEN="ghp_..."` is one GitHub-token finding named GITHUB_TOKEN, not also
a generic secret assignment. Only values with a recognizable shape are detected here.
Whether a plain string such as a team or project name is configuration is left to the
optional classifier in classify.py.

Nothing here modifies files. Credential values are masked in every Finding; the original
text is only reachable through the finding's span in the source file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .inventory import Inventory, SkippedFile, SourceFile, load_inventory

DETECTOR = "regex"
MASK = "********"
REDACTED = "[REDACTED:credential]"


class Category(StrEnum):
    EMAIL = "email"
    CREDENTIAL = "credential"
    FILE_PATH = "file_path"
    REPOSITORY = "repository"
    # A hard-coded value assigned to a configuration-like name, e.g. PROJECT_ID=my-project.
    CONFIGURATION = "configuration"
    # A reference to a variable by name, e.g. os.getenv("API_KEY"). It is already a
    # configuration interface, so it is reported but not replaced (design.md, section 3.2).
    ENVIRONMENT_VARIABLE = "environment_variable"


class FileKind(StrEnum):
    CODE = "code"  # only quoted values are literals; bare values are expressions
    MARKDOWN = "markdown"  # assignments only count in frontmatter and fenced code
    DATA = "data"  # config, shell and other files, where bare values are literals
    DOTENV = "dotenv"  # every assignment is configuration


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    category: Category
    rule: str
    confidence: float
    name: str = ""  # the variable or key the value is assigned to, when known
    prefix: str = ""  # public part of a credential kept in its masked value, e.g. "ghp_"


@dataclass(frozen=True)
class Finding:
    id: str  # stable across runs; derived from location and rule, never the value
    file: str
    line: int  # 1-based, of the first character
    column: int  # 1-based
    start: int  # character offsets into the decoded file
    end: int
    value: str  # masked for credentials
    category: Category
    rule: str
    confidence: float
    detector: str = DETECTOR
    name: str = ""

    @property
    def sensitive(self) -> bool:
        return self.category is Category.CREDENTIAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "start": self.start,
            "end": self.end,
            "value": self.value,
            "category": self.category.value,
            "rule": self.rule,
            "confidence": self.confidence,
            "detector": self.detector,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return Finding(
            id=data["id"],
            file=data["file"],
            line=data["line"],
            column=data["column"],
            start=data["start"],
            end=data["end"],
            value=data["value"],
            category=Category(data["category"]),
            rule=data["rule"],
            confidence=data["confidence"],
            detector=data.get("detector", DETECTOR),
            name=data.get("name", ""),
        )


@dataclass
class ScanResult:
    root: Path
    files: list[SourceFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Report form: file paths and findings, never file contents."""
        return {
            "files": [f.path for f in self.files],
            "skipped": [s.to_dict() for s in self.skipped],
            "findings": [f.to_dict() for f in self.findings],
        }


Detector = Callable[[SourceFile], Iterable[Candidate]]


# --- Shared value checks ----------------------------------------------------

_PLACEHOLDER_WORDS = (
    "your", "example", "changeme", "change_me", "placeholder", "redacted", "dummy",
    "xxxx", "...", "/path/to/", "owner/repo",
)
_LITERAL_WORDS = frozenset({
    "password", "pass", "secret", "token", "none", "null", "nil", "true", "false",
    "yes", "no", "undefined",
})


def _is_placeholder(value: str) -> bool:
    lower = value.lower()
    return (
        "{{" in value
        or "${" in value
        or value.startswith(("$", "%"))
        or (value.startswith("<") and value.endswith(">"))
        or set(value) <= set("*xX.-_")
        or lower in _LITERAL_WORDS
        or any(word in lower for word in _PLACEHOLDER_WORDS)
    )


def _words(name: str) -> list[str]:
    """Split apiKey, API_KEY, api-key and config.api.key into lowercase words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[\s_.\-]+", spaced.lower()) if w]


# --- Pattern rules ------------------------------------------------------------

@dataclass(frozen=True)
class PatternRule:
    """A detector that reports each match of one regex.

    group selects which part of the match is the finding. A group named prefix, when
    present, is the public part of a credential shown in place of the whole value.
    """

    rule: str
    category: Category
    confidence: float
    pattern: re.Pattern[str]
    group: int | str = 0
    keep: Callable[[re.Match[str]], bool] = lambda match: True

    def __call__(self, source: SourceFile) -> Iterator[Candidate]:
        for match in self.pattern.finditer(source.text):
            if self.keep(match):
                yield Candidate(
                    start=match.start(self.group),
                    end=match.end(self.group),
                    category=self.category,
                    rule=self.rule,
                    confidence=self.confidence,
                    prefix=match.groupdict().get("prefix") or "",
                )


def _is_real_token(match: re.Match[str]) -> bool:
    """False for documentation fillers such as sk-xxxxxxxx or AKIAIOSFODNN7EXAMPLE."""
    body = match[0][len(match.groupdict().get("prefix") or ""):]
    return len(set(body.lower()) - set("-_.")) > 1 and "example" not in body.lower()


def _credential(rule: str, pattern: str, **kwargs: Any) -> PatternRule:
    kwargs.setdefault("keep", _is_real_token)
    return PatternRule(rule, Category.CREDENTIAL, 1.0, re.compile(pattern), **kwargs)


CREDENTIAL_RULES = [
    _credential(
        "private_key",
        r"(?P<prefix>-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----)[\s\S]*?"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    _credential("github_token", r"(?<![A-Za-z0-9_])(?P<prefix>gh[pousr]_)[A-Za-z0-9]{36}(?!\w)"),
    _credential(
        "github_fine_grained_token",
        r"(?<![A-Za-z0-9_])(?P<prefix>github_pat_)[A-Za-z0-9]{22}_[A-Za-z0-9]{59}(?!\w)"),
    _credential("aws_access_key_id", r"(?<![A-Z0-9])(?P<prefix>A(?:KIA|SIA))[A-Z0-9]{16}(?![A-Z0-9])"),
    _credential("slack_token", r"(?<![\w-])(?P<prefix>xox[abposr]-)[A-Za-z0-9-]{10,}(?![\w-])"),
    _credential("google_api_key", r"(?<![\w-])(?P<prefix>AIza)[\w-]{35}(?![\w-])"),
    _credential(
        "sk_api_key",  # OpenAI and Anthropic style
        r"(?<![\w-])(?P<prefix>sk-(?:proj-|ant-(?:api\d\d-)?)?)[A-Za-z0-9_-]{20,}(?![\w-])"),
    _credential("stripe_secret_key", r"(?<!\w)(?P<prefix>[sr]k_live_)[A-Za-z0-9]{20,}(?!\w)"),
    _credential(
        "jwt",
        r"(?<![\w-])(?P<prefix>eyJ)[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}(?![\w-])"),
    _credential(
        "url_password",  # scheme://user:password@host; only the password is the finding
        r"\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:(?P<secret>[^\s@/'\"]+)@[^\s/'\"]",
        group="secret",
        keep=lambda m: not _is_placeholder(m["secret"])),
]


_GIT_HOSTS = r"github\.com|gitlab\.com|bitbucket\.org"
# First path segments that are site pages rather than an owner, e.g. github.com/settings/tokens.
_RESERVED_OWNERS = frozenset({
    "about", "apps", "collections", "contact", "customer-stories", "enterprise", "explore",
    "features", "issues", "login", "marketplace", "new", "notifications", "orgs", "pricing",
    "pulls", "readme", "search", "security", "settings", "site", "sponsors", "topics",
    "trending",
})
_PLACEHOLDER_OWNERS = frozenset({
    "owner", "user", "username", "org", "organization", "you", "your-org", "your-username",
    "yourname", "example",
})
_PLACEHOLDER_REPOS = frozenset({"repo", "repository", "your-repo", "example"})


def _is_real_repository(match: re.Match[str]) -> bool:
    owner, repo = match["owner"].lower(), match["repo"].lower().removesuffix(".git")
    return (owner not in _RESERVED_OWNERS
            and owner not in _PLACEHOLDER_OWNERS
            and repo not in _PLACEHOLDER_REPOS)


REPOSITORY_RULE = PatternRule(
    "repository_url",
    Category.REPOSITORY,
    0.6,  # a public repository may be reusable documentation
    re.compile(
        rf"(?<![\w.@-])(?:(?:https?|ssh|git)://(?:[\w.-]+@)?|git@)?(?:www\.)?(?:{_GIT_HOSTS})"
        r"[/:](?P<owner>[\w.-]+)/(?P<repo>[\w-]+(?:\.[\w-]+)*)(?![\w-])"),
    keep=_is_real_repository,
)


_RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_EMAIL_TLDS = frozenset({"example", "test", "invalid", "localhost"})
_PLACEHOLDER_LOCAL_PARTS = frozenset({
    "you", "your", "yourname", "your.name", "your_name", "youremail", "your.email",
    "username", "name", "someone", "somebody", "me", "email",
})


def _is_real_email(match: re.Match[str]) -> bool:
    domain, local = match["domain"].lower(), match["local"].lower()
    if local == "git":  # git@github.com:owner/repo SSH syntax
        return False
    if local in _PLACEHOLDER_LOCAL_PARTS:
        return False
    if domain.rsplit(".", 1)[-1] in _RESERVED_EMAIL_TLDS:
        return False
    return not any(domain == d or domain.endswith("." + d) for d in _RESERVED_EMAIL_DOMAINS)


EMAIL_RULE = PatternRule(
    "email",
    Category.EMAIL,
    0.9,
    re.compile(
        r"(?<![\w.+%-])(?P<local>[A-Za-z0-9][\w.+%-]*)@"
        r"(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-]|\.[\w-])"),
    keep=_is_real_email,
)


# Usernames that stand for "whoever runs this": documentation placeholders, shared
# accounts, and the default users of CI runners and cloud images.
_GENERIC_USERS = frozenset({
    "user", "username", "you", "yourname", "your-name", "your_name", "yourusername", "me",
    "name", "example", "shared", "runner", "guest", "default", "public", "ubuntu", "ec2-user",
})


def _is_personal_home(match: re.Match[str]) -> bool:
    return match["user"].lower() not in _GENERIC_USERS


# A path segment run that does not end in sentence punctuation.
_UNIX_TAIL = r"(?:/(?:[^\s\"'`<>|;,(){}\[\]]*[^\s\"'`<>|;,(){}\[\].:!?])?)?"
_WIN_SEP = r"(?:\\{1,2}|/)"
_WIN_TAIL = (rf"(?:{_WIN_SEP}(?:[^\s\"'`<>|*?;,(){{}}\[\]]*"
             r"[^\s\"'`<>|*?;,(){}\[\].:!\\])?)?")

FILE_PATH_RULES = [
    # Before the Unix rule, which would otherwise claim the /Users/... part of D:/Users/...
    PatternRule(
        "windows_home_path",
        Category.FILE_PATH,
        0.9,
        re.compile(
            rf"(?<![\w])[A-Za-z]:{_WIN_SEP}(?:Users|Documents and Settings){_WIN_SEP}"
            rf"(?P<user>[^\\/\s\"'`<>|:*?]*[^\\/\s\"'`<>|:*?.]){_WIN_TAIL}",
            re.IGNORECASE),
        keep=_is_personal_home,
    ),
    PatternRule(
        "unix_home_path",
        Category.FILE_PATH,
        0.9,
        re.compile(
            r"(?<![\w.~$-])/(?:Users|home)/"
            rf"(?P<user>[A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?){_UNIX_TAIL}"),
        keep=_is_personal_home,
    ),
]


# --- Assignments --------------------------------------------------------------

_SECRET_WORDS = frozenset({
    "token", "secret", "password", "passwd", "pwd", "passphrase", "apikey", "credential",
    "credentials",
})
_SECRET_PAIRS = frozenset({("api", "key"), ("access", "key"), ("private", "key"), ("auth", "key")})
# A name with any of these describes a secret rather than holding one, e.g. token_url.
_NOT_SECRET_WORDS = frozenset({
    "url", "uri", "endpoint", "path", "file", "dir", "env", "var", "type", "name", "header",
    "count", "limit", "length", "size", "max", "min", "id", "prompt", "description", "help",
    "field", "source",
})
# A name ending in one of these holds a location; only absolute ones are user-specific.
_PATH_WORDS = frozenset({"path", "dir", "directory", "folder"})
# A name ending in one of these is a setting about the thing, e.g. bucket_size.
_NOT_CONFIG_LAST_WORDS = frozenset({
    "size", "count", "limit", "timeout", "preference", "service", "mode", "type", "format",
    "enabled", "level", "version",
})
_TYPE_NAMES = frozenset({
    "str", "string", "int", "integer", "bool", "boolean", "float", "number", "list", "dict",
    "object", "array", "any", "path", "none",
})
_LOCAL_HOST = re.compile(r"(?:[a-z]+://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(?:/\S*)?")
_ABSOLUTE_PATH = re.compile(r"/|[A-Za-z]:[\\/]|\\\\")
_CONFIG_WORDS = frozenset({
    "path", "dir", "directory", "folder", "repo", "repository", "project", "account", "org",
    "organization", "organisation", "tenant", "workspace", "bucket", "region", "zone",
    "endpoint", "host", "hostname", "domain", "owner", "team", "channel", "user", "username",
    "email", "cluster", "namespace", "database", "db", "server",
})

_ASSIGNMENT = re.compile(
    r"""(?<![\w.$])(?<!\$\{)(?P<q>["']?)(?P<name>[A-Za-z_][\w.-]*)(?P=q)[ \t]*"""
    r"""(?P<sep>:=|[:=])[ \t]*"""
    r"""(?:"(?P<dq>[^"\n]*)"|'(?P<sq>[^'\n]*)'|(?P<bare>[^\s"'#,;(){}\[\]]+))""")
_FENCE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[\w+-]*)[^\n]*\n(?P<body>.*?)"
    r"(?:^[ \t]*(?P=fence)[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL)
_FRONTMATTER = re.compile(r"\A\ufeff?---[ \t]*\n(?P<body>.*?)^---[ \t]*$", re.MULTILINE | re.DOTALL)
_CODE_LANGS = frozenset({"python", "py", "javascript", "js", "typescript", "ts", "node"})
_CODE_SUFFIXES = frozenset({".py", ".js", ".ts"})
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_IDENTIFIER_CHAIN = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_FILE_WITH_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}")


def file_kind(source: SourceFile) -> FileKind:
    path = Path(source.path)
    if path.name == ".env" or path.name.startswith(".env."):
        return FileKind.DOTENV
    suffix = path.suffix.lower()
    if suffix in _CODE_SUFFIXES:
        return FileKind.CODE
    if suffix == ".md":
        return FileKind.MARKDOWN
    if not suffix and source.text.startswith("#!"):
        shebang = source.text.split("\n", 1)[0]
        if "python" in shebang or "node" in shebang:
            return FileKind.CODE
    return FileKind.DATA


def _assignment_regions(source: SourceFile) -> list[tuple[int, int, FileKind]]:
    """Spans of the file where key/value assignments are read, and how to read them."""
    kind = file_kind(source)
    if kind is not FileKind.MARKDOWN:
        return [(0, len(source.text), kind)]
    regions = []
    frontmatter = _FRONTMATTER.match(source.text)
    if frontmatter:
        regions.append((frontmatter.start("body"), frontmatter.end("body"), FileKind.DATA))
    for fence in _FENCE.finditer(source.text):
        lang = FileKind.CODE if fence["lang"].lower() in _CODE_LANGS else FileKind.DATA
        regions.append((fence.start("body"), fence.end("body"), lang))
    return regions


def _is_secret_name(words: list[str]) -> bool:
    if any(w in _NOT_SECRET_WORDS for w in words):
        return False
    return (any(w in _SECRET_WORDS for w in words)
            or any(pair in _SECRET_PAIRS for pair in zip(words, words[1:])))


def _looks_like_secret(value: str, bare: bool) -> bool:
    # Words such as "mypassword" or "not-needed" are documentation, not secrets.
    random_looking = len(value) >= 20 or (
        any(c.isdigit() for c in value) and any(c.isalpha() for c in value))
    return random_looking and not (
        len(value) < 8
        or any(c.isspace() for c in value)
        or value.isdigit()
        or _is_placeholder(value)
        or _ENV_NAME.fullmatch(value)  # names a variable rather than holding a secret
        or "://" in value
        or value.startswith(("/", "./", "../", "~"))
        or (bare and _IDENTIFIER_CHAIN.fullmatch(value))
    )


def _is_relative_path(value: str) -> bool:
    """A path inside the skill, such as ./scripts/run.py or scripts/run.py."""
    if value.startswith(("./", "../", ".\\", "..\\")):
        return True
    if value.startswith(("/", "~")) or "://" in value or "/" not in value:
        return False
    extension = _FILE_WITH_EXTENSION.search(value.rsplit("/", 1)[-1])
    return extension is not None and extension.group() != ".git"


def _is_config_name(words: list[str], kind: FileKind) -> bool:
    if kind is FileKind.DOTENV:
        return True
    return (bool(words) and words[-1] not in _NOT_CONFIG_LAST_WORDS
            and any(w in _CONFIG_WORDS for w in words))


def _looks_like_config(value: str, words: list[str]) -> bool:
    if words and words[-1] in _PATH_WORDS and not _ABSOLUTE_PATH.match(value):
        return False
    return not (
        len(value) < 2
        or not any(c.isalnum() for c in value)
        or "$" in value  # expanded at runtime
        or _is_placeholder(value)
        or value.startswith("~")
        or value.lower() in _TYPE_NAMES
        or _LOCAL_HOST.fullmatch(value)
        or _is_relative_path(value)
    )


def _is_key_position(text: str, match: re.Match[str]) -> bool:
    """Whether a match is a real key rather than words inside a string or a sentence.

    `name = value` counts anywhere, as in `docker run -e TOKEN=...`. An unquoted
    `name: value` only counts where a YAML or object key can start, so prose such as
    "Owner: see below" inside a description does not.
    """
    start = match.start()
    if start and text[start - 1] in "\"'":  # a word inside a string, as in "Email: "
        return False
    if match["q"] or match["sep"] != ":":
        return True
    before = text[text.rfind("\n", 0, start) + 1:start].strip()
    return before == "" or before.endswith(("-", "{", ","))


def detect_assignments(source: SourceFile) -> Iterator[Candidate]:
    """Values assigned to secret-like or configuration-like names.

    Code files only count quoted values, since `token = get_token()` is not a literal.
    """
    for start, end, kind in _assignment_regions(source):
        for match in _ASSIGNMENT.finditer(source.text, start, end):
            if not _is_key_position(source.text, match):
                continue
            group = next(g for g in ("dq", "sq", "bare") if match[g] is not None)
            bare = group == "bare"
            if bare and kind is FileKind.CODE:
                continue
            name, value = match["name"], match[group]
            if value != value.strip():
                continue
            words = _words(name)
            if _is_secret_name(words):
                if not _looks_like_secret(value, bare):
                    continue
                category, rule, confidence = Category.CREDENTIAL, "secret_assignment", 0.8
            elif _is_config_name(words, kind):
                if not _looks_like_config(value, words):
                    continue
                category, rule, confidence = Category.CONFIGURATION, "config_assignment", 0.6
            else:
                continue
            yield Candidate(match.start(group), match.end(group), category, rule, confidence,
                            name=name)


# --- Environment variable references ------------------------------------------

_ENV_REFERENCE = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*["'](?P<getenv>[A-Za-z_]\w*)["']"""
    r"""|os\.environ\[\s*["'](?P<environ>[A-Za-z_]\w*)["']\s*\]"""
    r"""|process\.env\.(?P<process>[A-Za-z_]\w*)"""
    r"""|process\.env\[\s*["'](?P<process_item>[A-Za-z_]\w*)["']\s*\]"""
    r"""|(?<![\\$])\$\{?(?P<shell>[A-Z][A-Z0-9_]*[A-Z0-9])(?!\w)""")
# Set by the shell or Hermes itself, so not configuration a skill author chose.
_BUILTIN_VARIABLES = frozenset({
    "HOME", "PATH", "PWD", "OLDPWD", "USER", "SHELL", "TMPDIR", "TMP", "LANG", "TERM",
    "HOSTNAME", "UID", "IFS", "RANDOM", "PPID", "LINENO", "SECONDS", "HERMES_SKILL_DIR",
})


# Shell variables the file sets itself without exporting are script locals.
_SHELL_LOCAL = re.compile(
    r"^[ \t]*(?:(?:local|readonly|declare(?:[ \t]+-\w+)*)[ \t]+)?(?P<assigned>[A-Z][A-Z0-9_]*)="
    r"|\bfor[ \t]+(?P<loop>[A-Z][A-Z0-9_]*)[ \t]+in\b"
    r"|\bread(?:[ \t]+-\w+)*[ \t]+(?P<read>[A-Z][A-Z0-9_]*)",
    re.MULTILINE)


def _shell_locals(text: str) -> set[str]:
    return {next(v for v in m.groups() if v) for m in _SHELL_LOCAL.finditer(text)}


def detect_env_references(source: SourceFile) -> Iterator[Candidate]:
    """References to variables by name.

    A $NAME the same file assigns without export is a script local, so it is not reported.
    """
    local = _shell_locals(source.text)
    for match in _ENV_REFERENCE.finditer(source.text):
        group = next(g for g, v in match.groupdict().items() if v is not None)
        name = match[group]
        if name in _BUILTIN_VARIABLES or (group == "shell" and name in local):
            continue
        yield Candidate(match.start(group), match.end(group), Category.ENVIRONMENT_VARIABLE,
                        f"env_reference_{group}", 1.0, name=name)


# --- Running detectors --------------------------------------------------------

# Earlier detectors win when spans overlap, so the most specific come first.
DETECTORS: list[Detector] = [
    *CREDENTIAL_RULES,
    REPOSITORY_RULE,
    EMAIL_RULE,
    *FILE_PATH_RULES,
    detect_assignments,
    detect_env_references,
]


def _finding_id(file: str, candidate: Candidate) -> str:
    key = f"{file}:{candidate.start}:{candidate.end}:{candidate.rule}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _to_finding(source: SourceFile, candidate: Candidate) -> Finding:
    text = source.text
    line_start = text.rfind("\n", 0, candidate.start) + 1
    if candidate.category is Category.CREDENTIAL:
        value = candidate.prefix + MASK
    else:
        value = text[candidate.start:candidate.end]
    return Finding(
        id=_finding_id(source.path, candidate),
        file=source.path,
        line=text.count("\n", 0, candidate.start) + 1,
        column=candidate.start - line_start + 1,
        start=candidate.start,
        end=candidate.end,
        value=value,
        category=candidate.category,
        rule=candidate.rule,
        confidence=candidate.confidence,
        name=candidate.name,
    )


def detect_file(source: SourceFile, detectors: Iterable[Detector] = DETECTORS) -> list[Finding]:
    kept: list[Candidate] = []
    for detector in detectors:
        for candidate in detector(source):
            if candidate.start == candidate.end:
                continue
            clash = next((i for i, k in enumerate(kept)
                          if candidate.start < k.end and k.start < candidate.end), None)
            if clash is None:
                kept.append(candidate)
                continue
            # A value already claimed by a specific rule takes the name it is assigned to.
            other = kept[clash]
            if (candidate.name and not other.name
                    and candidate.start <= other.start and other.end <= candidate.end):
                kept[clash] = replace(other, name=candidate.name)
    kept.sort(key=lambda c: (c.start, c.end))
    return [_to_finding(source, c) for c in kept]


def detect(inventory: Inventory, detectors: Iterable[Detector] = DETECTORS) -> ScanResult:
    detectors = list(detectors)
    findings = [f for source in inventory.files for f in detect_file(source, detectors)]
    return ScanResult(inventory.root, inventory.files, inventory.skipped, findings)


def scan_skill(skill_path: Path | str, **limits: int) -> ScanResult:
    """Inventory a skill and run the deterministic detectors over it."""
    return detect(load_inventory(skill_path, **limits))


def redact(text: str, findings: Iterable[Finding],
           categories: frozenset[Category] = frozenset({Category.CREDENTIAL})) -> str:
    """Text with the spans of the given categories replaced, keeping line numbers aligned.

    Run this before any file content leaves the machine, e.g. for an LLM.
    """
    spans = sorted((f.start, f.end) for f in findings if f.category in categories)
    parts, last = [], 0
    for start, end in spans:
        if start < last:
            continue
        parts.append(text[last:start])
        parts.append(REDACTED + "\n" * text.count("\n", start, end))
        last = end
    parts.append(text[last:])
    return "".join(parts)
