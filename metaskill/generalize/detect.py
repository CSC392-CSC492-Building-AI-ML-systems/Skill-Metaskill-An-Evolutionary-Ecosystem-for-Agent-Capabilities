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

import functools
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
    PHONE = "phone"
    CREDENTIAL = "credential"
    FILE_PATH = "file_path"
    REPOSITORY = "repository"
    # An internal hostname, a company's subdomain on a hosted service, or a private IP.
    ORGANIZATION_HOST = "organization_host"
    # A hard-coded value assigned to a configuration-like name, e.g. PROJECT_ID=my-project.
    CONFIGURATION = "configuration"
    # A reference to a variable by name, e.g. os.getenv("API_KEY"). It is already a
    # configuration interface, so it is reported but not replaced (design.md, section 3.2).
    ENVIRONMENT_VARIABLE = "environment_variable"


class Action(StrEnum):
    """What generalization should do with a finding (design.md, section 3.2 step 4)."""

    REPLACE = "replace"  # high confidence: replace automatically
    REVIEW = "review"  # ambiguous: leave unchanged and list for a decision
    KEEP = "keep"  # already a configuration interface


REPLACE_THRESHOLD = 0.9


def action_for(category: Category, confidence: float) -> Action:
    if category is Category.ENVIRONMENT_VARIABLE:
        return Action.KEEP
    if category is Category.CREDENTIAL or confidence >= REPLACE_THRESHOLD:
        return Action.REPLACE
    return Action.REVIEW


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
    action: Action
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
            "action": self.action.value,
            "detector": self.detector,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        category = Category(data["category"])
        action = data.get("action")
        return Finding(
            id=data["id"],
            file=data["file"],
            line=data["line"],
            column=data["column"],
            start=data["start"],
            end=data["end"],
            value=data["value"],
            category=category,
            rule=data["rule"],
            confidence=data["confidence"],
            action=Action(action) if action else action_for(category, data["confidence"]),
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
        or (value.startswith("{") and value.endswith("}"))  # a template slot, e.g. {QUERY}
        or lower.startswith(("my-", "my_"))  # as in my-project or my_bucket
        or set(value) <= set("*xX.-_")
        or lower in _LITERAL_WORDS
        or any(word in lower for word in _PLACEHOLDER_WORDS)
    )


def _words(name: str) -> list[str]:
    """Split apiKey, API_KEY, api-key and config.api.key into lowercase words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[\s_.\-]+", spaced.lower()) if w]


# --- Pattern rules ------------------------------------------------------------

Grade = tuple[str, float]  # rule name and confidence


@dataclass(frozen=True)
class PatternRule:
    """A detector that reports each match of one regex.

    group selects which part of the match is the finding. A group named prefix, when
    present, is the public part of a credential shown in place of the whole value.
    keep drops a match. grade, when set, picks a more specific rule name and confidence
    for a match, or drops it by returning None.
    """

    rule: str
    category: Category
    confidence: float
    pattern: re.Pattern[str]
    group: int | str = 0
    keep: Callable[[re.Match[str]], bool] = lambda match: True
    grade: Callable[[re.Match[str], SourceFile], Grade | None] | None = None

    def __call__(self, source: SourceFile) -> Iterator[Candidate]:
        for match in self.pattern.finditer(source.text):
            if not self.keep(match):
                continue
            graded = self.grade(match, source) if self.grade else (self.rule, self.confidence)
            if graded is None:
                continue
            yield Candidate(
                start=match.start(self.group),
                end=match.end(self.group),
                category=self.category,
                rule=graded[0],
                confidence=graded[1],
                prefix=match.groupdict().get("prefix") or "",
            )


def _command_before(match: re.Match[str]) -> str:
    """The text before a match on its line, including lines continued with a backslash."""
    text = match.string
    start = text.rfind("\n", 0, match.start()) + 1
    while start > 1 and text[start - 2] == "\\":
        start = text.rfind("\n", 0, start - 1) + 1
    return text[start:match.start()]


def _is_real_token(match: re.Match[str]) -> bool:
    """False for documentation fillers such as sk-xxxxxxxx or AKIAIOSFODNN7EXAMPLE.

    The longest alphanumeric run is checked so that a filler in the secret part of a
    multi-part token, as in .../T00000000/B00000000/XXXXXXXXXXXXXXXX, is caught too.
    """
    body = match[0][len(match.groupdict().get("prefix") or ""):]
    longest = max(re.findall(r"[A-Za-z0-9]+", body), key=len, default="")
    return len(set(longest.lower())) > 1 and "example" not in body.lower()


def _is_real_private_key(match: re.Match[str]) -> bool:
    """False for a key block whose body is a placeholder such as <your key here>."""
    body = match[0][len(match["prefix"]):match[0].rfind("-----END")]
    return re.search(r"[A-Za-z0-9+/=]{16,}", body) is not None


def _credential(rule: str, pattern: str, **kwargs: Any) -> PatternRule:
    kwargs.setdefault("keep", _is_real_token)
    return PatternRule(rule, Category.CREDENTIAL, 1.0, re.compile(pattern), **kwargs)


CREDENTIAL_RULES = [
    _credential(
        "private_key",
        r"(?P<prefix>-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----)[\s\S]*?"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
        keep=_is_real_private_key),
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
    _credential("huggingface_token", r"(?<![\w-])(?P<prefix>hf_)[A-Za-z0-9]{30,}(?!\w)"),
    _credential("npm_token", r"(?<![\w-])(?P<prefix>npm_)[A-Za-z0-9]{36}(?!\w)"),
    _credential("gitlab_token", r"(?<![\w-])(?P<prefix>glpat-)[A-Za-z0-9_-]{20,}(?![\w-])"),
    _credential("pypi_token", r"(?<![\w-])(?P<prefix>pypi-)AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}"),
    _credential("sendgrid_key", r"(?<![\w-])(?P<prefix>SG\.)[\w-]{22}\.[\w-]{43}(?![\w-])"),
    _credential(
        "slack_webhook",
        r"(?P<prefix>https://hooks\.slack\.com/services/)T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/"
        r"[A-Za-z0-9]{16,}"),
    _credential(
        "discord_webhook",
        r"(?P<prefix>https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/)\d{15,20}/"
        r"[\w-]{40,}"),
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


# Paths below a repository that cite one of its pages rather than use the repository.
_REPOSITORY_PAGE = re.compile(
    r"/(?:blob|tree|issues|pull|pulls|releases|wiki|discussions|commit|commits|compare|"
    r"actions|raw|tags|graphs)\b|#")


def _grade_repository(match: re.Match[str], source: SourceFile) -> Grade:
    """A repository cited in documentation is less likely to be configuration.

    Cited means linked from markdown prose, or a link to one of its pages. A repository
    in a command, a config file or code (git clone, pip install git+...) is used.
    """
    text, start, end = match.string, match.start(), match.end()
    cited = (text[max(start - 2, 0):start] == "](" or text[start - 1:start] == "<"
             or _REPOSITORY_PAGE.match(text, end) is not None
             or (file_kind(source) is FileKind.MARKDOWN
                 and not any(s <= start < e for s, e, _ in _assignment_regions(source))))
    return ("repository_reference", 0.3) if cited else ("repository_url", 0.6)


REPOSITORY_RULE = PatternRule(
    "repository_url",
    Category.REPOSITORY,
    0.6,  # a public repository may be reusable documentation
    re.compile(
        rf"(?<![\w.@-])(?:(?:https?|ssh|git)://(?:[\w.-]+@)?|git@)?(?:www\.)?(?:{_GIT_HOSTS})"
        r"[/:](?P<owner>[\w.-]+)/(?P<repo>[\w-]+(?:\.[\w-]+)*)(?![\w-])"),
    keep=_is_real_repository,
    grade=_grade_repository,
)


_RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_EMAIL_TLDS = frozenset({"example", "test", "invalid", "localhost"})
_PLACEHOLDER_LOCAL_PARTS = frozenset({
    "you", "your", "yourname", "your.name", "your_name", "youremail", "your.email",
    "username", "name", "someone", "somebody", "me", "email",
})
# Shared mailboxes of an organization, usually published, rather than a person's address.
_ROLE_LOCAL_PARTS = frozenset({
    "privacy", "privacyrequest", "support", "help", "info", "contact", "sales", "security",
    "abuse", "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "webmaster",
    "hostmaster", "legal", "press", "media", "hello", "feedback", "billing", "dpo", "gdpr",
    "optout", "opt-out", "careers", "jobs", "team", "admin", "office", "marketing",
    "partners", "notifications", "alerts", "service", "enquiries", "inquiries",
    "customer-service", "customerservice", "customer.service", "privacy-request",
})
# Stock names used in documentation and tests.
_SAMPLE_LOCAL_PARTS = frozenset({
    "alice", "bob", "carol", "charlie", "dave", "eve", "mallory", "trent", "john.doe",
    "jane.doe", "johndoe", "janedoe", "jdoe", "john", "jane", "john.smith", "jane.smith",
    "jsmith", "foo", "bar", "test", "demo", "sample", "boss",
})
_SSH_COMMAND = re.compile(r"\b(?:ssh|scp|sftp|rsync|mosh)\b")


def _is_real_email(match: re.Match[str]) -> bool:
    domain, local = match["domain"].lower(), match["local"].lower()
    if local == "git":  # git@github.com:owner/repo SSH syntax
        return False
    if local in _PLACEHOLDER_LOCAL_PARTS or _is_placeholder(local):
        return False
    if domain.rsplit(".", 1)[-1] in _RESERVED_EMAIL_TLDS:
        return False
    return not any(domain == d or domain.endswith("." + d) for d in _RESERVED_EMAIL_DOMAINS)


def _grade_email(match: re.Match[str], source: SourceFile) -> Grade:
    local = match["local"].lower()
    if _SSH_COMMAND.search(_command_before(match)):
        return "ssh_destination", 0.5  # user@host for a remote login, not a mailbox
    if local in _ROLE_LOCAL_PARTS:
        return "role_email", 0.5
    if local in _SAMPLE_LOCAL_PARTS:
        return "sample_email", 0.5
    return "email", 0.9


EMAIL_RULE = PatternRule(
    "email",
    Category.EMAIL,
    0.9,
    re.compile(
        r"(?<![\w.+%-])(?P<local>[A-Za-z0-9][\w.+%-]*)@"
        r"(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w-]|\.[\w-])"),
    keep=_is_real_email,
    grade=_grade_email,
)


# --- Phone numbers ------------------------------------------------------------

_TOLL_FREE_AREA_CODES = frozenset({"800", "833", "844", "855", "866", "877", "888"})


def _grade_phone(match: re.Match[str], source: SourceFile) -> Grade | None:
    """Drop numbers that cannot be real or are reserved for examples, e.g. 555-0123.

    North American numbers, whether written +1 702 555 1234 or (702) 555-1234, are
    checked against the numbering plan. Other international numbers only by length.
    """
    digits = re.sub(r"\D", "", match[0])
    if match["intl"]:
        if not 8 <= len(digits) <= 15 or match[0].count(".") == 1:  # +4.0767 is a number
            return None
        national = digits[1:] if digits.startswith("1") else None
        if national is not None and len(national) != 10:
            return None
    else:
        national = digits[-10:]
    if len(set(digits)) <= 2 or "1234567" in digits:
        return None
    if national is not None:
        area, exchange = national[:3], national[3:6]
        if area[0] in "01" or exchange[0] in "01" or "555" in (area, exchange):
            return None
        if area in _TOLL_FREE_AREA_CODES:
            return "toll_free_phone", 0.5  # a business line, usually published
    return "phone_number", 0.9


PHONE_RULE = PatternRule(
    "phone_number",
    Category.PHONE,
    0.9,
    re.compile(
        r"(?<![\w+./-])"
        r"(?:(?P<intl>\+\d{1,3}(?:[ .-]?\(?\d{1,4}\)?){1,6})"
        r"|(?:1[ .-])?(?:\(\d{3}\)[ .-]?|\d{3}[.-])\d{3}[.-]\d{4})"
        r"(?![\w-]|[.:]\d)"),
    grade=_grade_phone,
)


# Usernames that stand for "whoever runs this": documentation placeholders, shared
# accounts, and the default users of CI runners and cloud images.
_GENERIC_USERS = frozenset({
    "user", "username", "you", "yourname", "your-name", "your_name", "yourusername", "me",
    "name", "example", "shared", "runner", "guest", "default", "public", "ubuntu", "ec2-user",
})


def _is_personal_home(match: re.Match[str]) -> bool:
    return match["user"].lower() not in _GENERIC_USERS


# --- Organization hosts -------------------------------------------------------

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# Hosts every Docker or cloud user shares, so not specific to one organization.
_SHARED_INTERNAL_HOSTS = frozenset({
    "host.docker.internal", "gateway.docker.internal", "kubernetes.docker.internal",
    "host.containers.internal", "metadata.google.internal",
})


def _is_org_internal_host(match: re.Match[str]) -> bool:
    host = match["host"].lower()
    if host in _SHARED_INTERNAL_HOSTS or _is_placeholder(host):
        return False
    if match["tld"].lower() != "local":
        return True
    # .local is also a common attribute name (threading.local), so it needs URL context.
    text = match.string
    return (text[max(match.start() - 3, 0):match.start()] == "://"
            or text[match.start() - 1:match.start()] == "@"
            or re.match(r":\d", text[match.end():match.end() + 2]) is not None)


# Hosted services where the first label of the hostname names the customer.
_TENANT_SUFFIXES = (
    r"atlassian\.net|slack\.com|zendesk\.com|sharepoint\.com|okta\.com|oktapreview\.com|"
    r"auth0\.com|my\.salesforce\.com|lightning\.force\.com|myshopify\.com|freshdesk\.com|"
    r"service-now\.com|ghe\.com|visualstudio\.com|azurewebsites\.net|blob\.core\.windows\.net|"
    r"vault\.azure\.net|database\.windows\.net|firebaseio\.com|firebaseapp\.com|supabase\.co|"
    r"herokuapp\.com|vercel\.app|netlify\.app|github\.io|gitlab\.io|snowflakecomputing\.com|"
    r"cloud\.databricks\.com|grafana\.net|sentry\.io|pagerduty\.com|notion\.site|"
    r"workers\.dev|pages\.dev|onrender\.com|fly\.dev|awsapps\.com|"
    r"s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com"
)
# First labels that are the service's own pages, or stand for "your company".
_NOT_TENANTS = frozenset({
    "www", "api", "app", "apps", "docs", "doc", "help", "support", "status", "developer",
    "developers", "dev", "login", "auth", "hooks", "files", "cdn", "static", "community",
    "blog", "admin", "id", "accounts", "account", "dashboard", "console", "portal", "my",
    "ingest", "us", "eu", "global", "marketplace", "partners", "trust", "security", "s3",
    "company", "yourcompany", "tenant", "yourtenant", "org", "yourorg", "team", "yourteam",
    "workspace", "yourworkspace", "subdomain", "yourdomain", "domain", "store", "shop",
    "mystore", "myshop", "myapp", "mysite", "myproject", "myorg", "mycompany", "project",
    "username", "user", "acme", "bucket", "mybucket", "name",
})


def _is_tenant_host(match: re.Match[str]) -> bool:
    tenant = match["tenant"].lower()
    return tenant not in _NOT_TENANTS and not _is_placeholder(tenant)


def _grade_tenant_host(match: re.Match[str], source: SourceFile) -> Grade:
    # GitHub and GitLab Pages sites are nearly always a project's public documentation.
    if match[0].lower().endswith((".github.io", ".gitlab.io")):
        return "pages_host", 0.3
    return "saas_tenant_host", 0.7


# Gateway, network and broadcast addresses appear in every router manual.
def _is_specific_private_ip(match: re.Match[str]) -> bool:
    octets = [int(o) for o in match[0].split(".")]
    return all(o <= 255 for o in octets) and octets[-1] not in (0, 1, 255)


# Account IDs AWS uses in its own documentation.
_SAMPLE_AWS_ACCOUNTS = frozenset({
    "123456789012", "111122223333", "444455556666", "012345678901", "210987654321",
})


def _is_real_aws_account(match: re.Match[str]) -> bool:
    account = match["account"]
    return account not in _SAMPLE_AWS_ACCOUNTS and len(set(account)) > 1


ORGANIZATION_RULES = [
    PatternRule(
        "internal_host",
        Category.ORGANIZATION_HOST,
        0.7,
        re.compile(
            rf"(?<![\w.-])(?P<host>(?:{_LABEL}\.)+"
            r"(?P<tld>internal|corp|lan|intranet|localdomain|home\.arpa|local))"
            r"(?![\w-]|\.[a-z0-9])",
            re.IGNORECASE),
        group="host",
        keep=_is_org_internal_host,
    ),
    PatternRule(
        "saas_tenant_host",
        Category.ORGANIZATION_HOST,
        0.7,
        re.compile(
            rf"(?<![\w.-])(?P<tenant>{_LABEL})\.(?:{_LABEL}\.)*?(?:{_TENANT_SUFFIXES})"
            r"(?![\w-]|\.[a-z0-9])",
            re.IGNORECASE),
        keep=_is_tenant_host,
        grade=_grade_tenant_host,
    ),
    PatternRule(
        "private_ip",
        Category.ORGANIZATION_HOST,
        0.5,  # often an example LAN address
        re.compile(
            r"(?<![\w.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}"
            r"(?![\w]|\.\d|/\d)"),
        keep=_is_specific_private_ip,
    ),
    PatternRule(
        "aws_account_id",
        Category.CONFIGURATION,
        0.8,
        re.compile(r"\barn:aws[\w-]*:[\w-]+:[\w-]*:(?P<account>\d{12}):"),
        group="account",
        keep=_is_real_aws_account,
    ),
]


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
    "enabled", "level", "version", "port", "interval", "retries", "delay", "ttl", "agent",
})
_TYPE_NAMES = frozenset({
    "str", "string", "int", "integer", "bool", "boolean", "float", "number", "list", "dict",
    "object", "array", "any", "path", "none",
})
# Values every installation shares: HTTP methods and stock account names.
_GENERIC_VALUES = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "user", "admin", "root",
    "postgres", "default", "guest", "test", "demo", "sample",
    "__token__",  # the username PyPI expects with an API token
})
_LOCAL_HOST = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://(?:[^\s/@]+@)?)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(?:/\S*)?")
_ABSOLUTE_PATH = re.compile(r"/|[A-Za-z]:[\\/]|\\\\")
_CONFIG_WORDS = frozenset({
    "path", "dir", "directory", "folder", "repo", "repository", "project", "account", "org",
    "organization", "organisation", "tenant", "workspace", "bucket", "region", "zone",
    "endpoint", "host", "hostname", "domain", "owner", "team", "channel", "user", "username",
    "email", "cluster", "namespace", "database", "db", "server", "phone", "mobile",
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


@functools.lru_cache(maxsize=32)
def _assignment_regions(source: SourceFile) -> tuple[tuple[int, int, FileKind], ...]:
    """Spans of the file where key/value assignments are read, and how to read them."""
    kind = file_kind(source)
    if kind is not FileKind.MARKDOWN:
        return ((0, len(source.text), kind),)
    regions = []
    frontmatter = _FRONTMATTER.match(source.text)
    if frontmatter:
        regions.append((frontmatter.start("body"), frontmatter.end("body"), FileKind.DATA))
    for fence in _FENCE.finditer(source.text):
        lang = FileKind.CODE if fence["lang"].lower() in _CODE_LANGS else FileKind.DATA
        regions.append((fence.start("body"), fence.end("body"), lang))
    return tuple(regions)


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
    if any(w in _PATH_WORDS for w in words) and not _ABSOLUTE_PATH.match(value):
        return False
    return not (
        len(value) < 3
        or not any(c.isalnum() for c in value)
        or "$" in value  # expanded at runtime
        or _is_placeholder(value)
        or value.startswith(("~", "/dev/"))
        or value.lower() in _TYPE_NAMES
        or value.lower() in _GENERIC_VALUES
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
# Set by the shell, the OS, CI runners, launchers such as torchrun, or Hermes itself, so
# not configuration a skill author chose.
_BUILTIN_VARIABLES = frozenset({
    "HOME", "PATH", "PWD", "OLDPWD", "USER", "SHELL", "TMPDIR", "TMP", "LANG", "LANGUAGE",
    "LC_ALL", "TERM", "HOSTNAME", "UID", "EUID", "IFS", "RANDOM", "PPID", "LINENO",
    "SECONDS", "REPLY", "OSTYPE", "SHLVL", "PS1", "COLUMNS", "LINES", "BASH_SOURCE",
    "BASH_VERSION", "PIPESTATUS", "EDITOR", "VISUAL", "PAGER", "DISPLAY", "TZ",
    "SSH_AUTH_SOCK", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH", "CUDA_HOME", "CUDA_VISIBLE_DEVICES", "WORLD_SIZE", "RANK",
    "LOCAL_RANK", "NODE_RANK", "MASTER_ADDR", "MASTER_PORT", "CI", "GITHUB_ACTIONS",
    "GITHUB_WORKSPACE", "GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STEP_SUMMARY",
    "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "RUNNER_TEMP", "RUNNER_OS",
    "HERMES_SKILL_DIR", "HERMES_HOME",
})


# Shell variables the file sets itself without exporting are script locals.
_SHELL_LOCAL = re.compile(
    r"(?:^|[;&|({]|\b(?:then|do|else))[ \t]*"
    r"(?:(?:local|readonly|declare(?:[ \t]+-\w+)*)[ \t]+)?(?P<assigned>[A-Z][A-Z0-9_]*)="
    r"|\bfor[ \t]+(?P<loop>[A-Z][A-Z0-9_]*)[ \t]+in\b"
    r"|\bread(?:[ \t]+-\w+)*[ \t]+(?P<read>[A-Z][A-Z0-9_]*)",
    re.MULTILINE)
_SHELL_LANGS = frozenset({"bash", "sh", "shell", "zsh", "console", "shell-session"})


def _shell_locals(text: str) -> set[str]:
    return {next(v for v in m.groups() if v) for m in _SHELL_LOCAL.finditer(text)}


def _shell_regions(source: SourceFile) -> list[tuple[int, int]]:
    """Spans of the file that are shell code, where quoting rules decide expansion."""
    path = Path(source.path)
    if path.suffix.lower() == ".sh" or (
            not path.suffix and re.match(r"#!.*\b(?:ba|z)?sh\b", source.text)):
        return [(0, len(source.text))]
    if path.suffix.lower() != ".md":
        return []
    return [(f.start("body"), f.end("body")) for f in _FENCE.finditer(source.text)
            if f["lang"].lower() in _SHELL_LANGS]


def _in_single_quotes(text: str, position: int) -> bool:
    """Whether position sits inside a '...' string on its line, where $NAME is literal."""
    quote = None
    for char in text[text.rfind("\n", 0, position) + 1:position]:
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
    return quote == "'"


def detect_env_references(source: SourceFile) -> Iterator[Candidate]:
    """References to variables by name.

    A $NAME the same file assigns without export is a script local, so it is not reported,
    and neither is one inside single quotes in shell code, which the shell never expands
    (ast-grep patterns such as 'console.log($MSG)' use that syntax).
    """
    local = _shell_locals(source.text)
    shell = _shell_regions(source)
    for match in _ENV_REFERENCE.finditer(source.text):
        group = next(g for g, v in match.groupdict().items() if v is not None)
        name = match[group]
        if name in _BUILTIN_VARIABLES or (group == "shell" and name in local):
            continue
        if (group == "shell" and any(s <= match.start() < e for s, e in shell)
                and _in_single_quotes(source.text, match.start())):
            continue
        yield Candidate(match.start(group), match.end(group), Category.ENVIRONMENT_VARIABLE,
                        f"env_reference_{group}", 1.0, name=name)


# --- Running detectors --------------------------------------------------------

# Earlier detectors win when spans overlap, so the most specific come first.
DETECTORS: list[Detector] = [
    *CREDENTIAL_RULES,
    REPOSITORY_RULE,
    EMAIL_RULE,
    PHONE_RULE,
    *FILE_PATH_RULES,
    *ORGANIZATION_RULES,
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
        action=action_for(candidate.category, candidate.confidence),
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
