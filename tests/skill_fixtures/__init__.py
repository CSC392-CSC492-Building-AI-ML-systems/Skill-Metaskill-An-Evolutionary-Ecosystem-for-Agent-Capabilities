"""Small skills with known contents and the findings the detector should report for them.

Each fixture lists its files, the exact findings expected, the files inventory should
skip, and the secrets it contains so tests can check that none of them leak. File
contents are written as lists of lines so line numbers can be counted by eye.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from metaskill.generalize.detect import MASK, Action, Category
from metaskill.generalize.inventory import SkipReason

from .secrets import (
    AWS_SAMPLE_KEY,
    DB_PASSWORD,
    GENERIC_SECRET,
    GITHUB_TOKEN,
    HF_TOKEN,
    NPM_TOKEN,
    PRIVATE_KEY,
    PRIVATE_KEY_BODY,
    PYPI_TOKEN,
    SK_KEY,
    SLACK_WEBHOOK,
)

C, A = Category, Action


class Row(NamedTuple):
    """One expected finding. value is the reported value, so credentials appear masked."""

    file: str
    line: int
    category: Category
    rule: str
    confidence: float
    action: Action
    name: str
    value: str


@dataclass(frozen=True)
class SkillFixture:
    # Contents are left out of repr so failing tests never print the secrets in them.
    id: str
    files: dict[str, str | bytes] = field(repr=False)
    expected: list[Row]
    skipped: dict[str, SkipReason] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list, repr=False)
    symlinks: dict[str, str] = field(default_factory=dict)  # link -> target
    outside: dict[str, str] = field(default_factory=dict, repr=False)  # beside the skill


def lines(*text: str) -> str:
    return "\n".join(text) + "\n"


def materialize(fixture: SkillFixture, base: Path) -> Path:
    """Write the fixture under base and return the skill directory."""
    root = base / "skill"
    for rel, content in {**fixture.files,
                         **{f"../outside/{k}": v for k, v in fixture.outside.items()}}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
    for link, target in fixture.symlinks.items():
        resolved = (base / "outside" / target[len("outside:"):]
                    if target.startswith("outside:") else Path(target))
        os.symlink(resolved, root / link)
    return root


FRONTMATTER = ("---", "name: fixture", "description: A fixture skill", "---")

CLEAN = SkillFixture(
    id="no_sensitive_values",
    files={
        "SKILL.md": lines(
            "---",
            "name: pr-review",
            "description: Review pull requests for security issues",
            "version: 1.0.0",
            "required_environment_variables:",
            "  - name: GITHUB_TOKEN",
            "    prompt: Enter a GitHub token",
            "---",
            "# PR review",
            "",
            "Review pull requests for security vulnerabilities.",
            "Review pull requests from the Atlas Streams repository.",
            "Run `python ${HERMES_SKILL_DIR}/scripts/review.py --out ./out/report.md`.",
            "See https://docs.github.com/en/rest and https://github.com/owner/repo.",
            "Questions go to user@example.com. Retry after 30 seconds, max 3 times.",
        ),
        "scripts/review.py": lines(
            "import os",
            "import sys",
            "from pathlib import Path",
            "",
            'OUT_DIR = Path(__file__).parent / "out"',
            "MAX_RETRIES = 3",
            'print(sys.argv, os.environ.get("HOME"))',
        ),
    },
    expected=[],
)

CREDENTIALS = SkillFixture(
    id="credentials",
    files={
        ".env": lines(
            "# Service credentials",
            f"OPENAI_API_KEY={SK_KEY}",
            f"DB_PASSWORD={GENERIC_SECRET}",
        ),
        ".npmrc": lines(f"//registry.npmjs.org/:_authToken={NPM_TOKEN}"),
        ".pypirc": lines("[pypi]", "username = __token__", f"password = {PYPI_TOKEN}"),
        "scripts/deploy.py": lines(
            "import requests",
            "",
            f'GITHUB_TOKEN = "{GITHUB_TOKEN}"',
            f'HF_TOKEN = "{HF_TOKEN}"',
            f'DEPLOY_KEY = """{PRIVATE_KEY}"""',
        ),
        "SKILL.md": lines(
            *FRONTMATTER,
            "Post to Slack when the deploy finishes:",
            "",
            "```bash",
            f"curl -X POST -d '{{\"text\": \"done\"}}' {SLACK_WEBHOOK}",
            "```",
            "",
            f"Connect with `postgres://admin:{DB_PASSWORD}@db.internal:5432/app`.",
        ),
    },
    expected=[
        Row(".env", 2, C.CREDENTIAL, "sk_api_key", 1.0, A.REPLACE, "OPENAI_API_KEY",
            "sk-proj-" + MASK),
        Row(".env", 3, C.CREDENTIAL, "secret_assignment", 0.8, A.REPLACE, "DB_PASSWORD", MASK),
        Row(".npmrc", 1, C.CREDENTIAL, "npm_token", 1.0, A.REPLACE, "_authToken",
            "npm_" + MASK),
        Row(".pypirc", 3, C.CREDENTIAL, "pypi_token", 1.0, A.REPLACE, "password",
            "pypi-" + MASK),
        Row("SKILL.md", 8, C.CREDENTIAL, "slack_webhook", 1.0, A.REPLACE, "",
            "https://hooks.slack.com/services/" + MASK),
        Row("SKILL.md", 11, C.CREDENTIAL, "url_password", 1.0, A.REPLACE, "", MASK),
        Row("SKILL.md", 11, C.ORGANIZATION_HOST, "internal_host", 0.7, A.REVIEW, "",
            "db.internal"),
        Row("scripts/deploy.py", 3, C.CREDENTIAL, "github_token", 1.0, A.REPLACE,
            "GITHUB_TOKEN", "ghp_" + MASK),
        Row("scripts/deploy.py", 4, C.CREDENTIAL, "huggingface_token", 1.0, A.REPLACE,
            "HF_TOKEN", "hf_" + MASK),
        Row("scripts/deploy.py", 5, C.CREDENTIAL, "private_key", 1.0, A.REPLACE, "",
            "-----BEGIN RSA PRIVATE KEY-----" + MASK),
    ],
    secrets=[SK_KEY, GENERIC_SECRET, NPM_TOKEN, PYPI_TOKEN, GITHUB_TOKEN, HF_TOKEN,
             PRIVATE_KEY_BODY, SLACK_WEBHOOK, DB_PASSWORD],
)

EMAILS = SkillFixture(
    id="emails",
    files={
        "SKILL.md": lines(
            "---",
            "name: weekly-report",
            "description: Send the weekly report",
            "author: Jayant Arora (jayant.arora@utoronto.ca)",
            "---",
            "Send the report to jayant.arora@utoronto.ca and cc privacy@company.com.",
            "Examples: alice@co.com, user@example.com, you@gmail.com, npm i pkg@1.2.3.",
            "Deploy with `ssh deploy@build.company.com`.",
        ),
        "scripts/notify.py": lines('REPORT_EMAIL = "jayant.arora@utoronto.ca"'),
    },
    expected=[
        Row("SKILL.md", 4, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant.arora@utoronto.ca"),
        Row("SKILL.md", 6, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant.arora@utoronto.ca"),
        Row("SKILL.md", 6, C.EMAIL, "role_email", 0.5, A.REVIEW, "", "privacy@company.com"),
        Row("SKILL.md", 7, C.EMAIL, "sample_email", 0.5, A.REVIEW, "", "alice@co.com"),
        Row("SKILL.md", 8, C.EMAIL, "ssh_destination", 0.5, A.REVIEW, "",
            "deploy@build.company.com"),
        Row("scripts/notify.py", 1, C.EMAIL, "email", 0.9, A.REPLACE, "REPORT_EMAIL",
            "jayant.arora@utoronto.ca"),
    ],
)

PHONES = SkillFixture(
    id="phone_numbers",
    files={
        "SKILL.md": lines(
            *FRONTMATTER,
            "On-call: +1 (416) 978-2011 or 416-978-2011.",
            "UK office: +44 20 8642 9753.",
            "Support line: 1-800-266-8228 (toll free).",
            "Not phones: 555-0123, (555) 555-1234, version 1.2.3.4, released 2026-10-17,",
            "ISBN 978-0-306-40615-7, id 123-456-7890, coefficient +4.0767416621.",
        ),
        "references/contacts.json": lines('{"support_phone": "+1 416 978 2011"}'),
    },
    expected=[
        Row("SKILL.md", 5, C.PHONE, "phone_number", 0.9, A.REPLACE, "", "+1 (416) 978-2011"),
        Row("SKILL.md", 5, C.PHONE, "phone_number", 0.9, A.REPLACE, "", "416-978-2011"),
        Row("SKILL.md", 6, C.PHONE, "phone_number", 0.9, A.REPLACE, "", "+44 20 8642 9753"),
        Row("SKILL.md", 7, C.PHONE, "toll_free_phone", 0.5, A.REVIEW, "", "1-800-266-8228"),
        Row("references/contacts.json", 1, C.PHONE, "phone_number", 0.9, A.REPLACE,
            "support_phone", "+1 416 978 2011"),
    ],
)

LOCAL_PATHS = SkillFixture(
    id="local_paths",
    files={
        "SKILL.md": lines(
            *FRONTMATTER,
            "Notebooks live in /Users/jayant/Documents/notebooks.",
            r"On Windows use C:\Users\Jayant\notebooks.",
            "Run `python ./scripts/sync.py` or `python ${HERMES_SKILL_DIR}/scripts/sync.py`.",
            "Temporary files go to /tmp/sync and ~/.cache/sync; see ../references/api.md.",
        ),
        "scripts/sync.py": lines(
            "from pathlib import Path",
            "",
            'OUTPUT_DIR = "/home/jayant/sync-output"',
            'CACHE_DIR = Path.home() / ".cache"',
            'LOG_FILE = "/var/log/sync.log"',  # a standard system location
            'TEMPLATE_PATH = "templates/report.md"',
            'DATA_DIR = "/srv/atlas/sync"',
        ),
    },
    expected=[
        Row("SKILL.md", 5, C.FILE_PATH, "unix_home_path", 0.9, A.REPLACE, "",
            "/Users/jayant/Documents/notebooks"),
        Row("SKILL.md", 6, C.FILE_PATH, "windows_home_path", 0.9, A.REPLACE, "",
            r"C:\Users\Jayant\notebooks"),
        Row("scripts/sync.py", 3, C.FILE_PATH, "unix_home_path", 0.9, A.REPLACE, "OUTPUT_DIR",
            "/home/jayant/sync-output"),
        Row("scripts/sync.py", 7, C.CONFIGURATION, "config_assignment", 0.6, A.REVIEW,
            "DATA_DIR", "/srv/atlas/sync"),
    ],
)

ORGANIZATION = SkillFixture(
    id="organization_settings",
    files={
        "SKILL.md": lines(
            *FRONTMATTER,
            "Dashboards: https://grafana.corp/d/incidents and https://atlas-streams.atlassian.net.",
            "Internal API at api.platform.internal; Docker reaches host.docker.internal.",
            "Staging box: 10.20.30.40. Docs: https://nvidia.github.io/TensorRT-LLM/.",
            "Review incidents for the Atlas Streams team.",
        ),
        "config/settings.yaml": lines(
            "team: atlas-streams",
            "region: us-east-1",
            "aws_role: arn:aws:iam::493817261504:role/triage",
            "wiki_host: atlas-streams.atlassian.net",
            "sample_role: arn:aws:iam::123456789012:role/demo",
        ),
    },
    expected=[
        Row("SKILL.md", 5, C.ORGANIZATION_HOST, "internal_host", 0.7, A.REVIEW, "",
            "grafana.corp"),
        Row("SKILL.md", 5, C.ORGANIZATION_HOST, "saas_tenant_host", 0.7, A.REVIEW, "",
            "atlas-streams.atlassian.net"),
        Row("SKILL.md", 6, C.ORGANIZATION_HOST, "internal_host", 0.7, A.REVIEW, "",
            "api.platform.internal"),
        Row("SKILL.md", 7, C.ORGANIZATION_HOST, "private_ip", 0.5, A.REVIEW, "", "10.20.30.40"),
        Row("SKILL.md", 7, C.ORGANIZATION_HOST, "pages_host", 0.3, A.REVIEW, "",
            "nvidia.github.io"),
        Row("config/settings.yaml", 1, C.CONFIGURATION, "config_assignment", 0.6, A.REVIEW,
            "team", "atlas-streams"),
        Row("config/settings.yaml", 2, C.CONFIGURATION, "config_assignment", 0.6, A.REVIEW,
            "region", "us-east-1"),
        Row("config/settings.yaml", 3, C.CONFIGURATION, "aws_account_id", 0.8, A.REVIEW,
            "", "493817261504"),
        Row("config/settings.yaml", 4, C.ORGANIZATION_HOST, "saas_tenant_host", 0.7, A.REVIEW,
            "wiki_host", "atlas-streams.atlassian.net"),
    ],
)

REPEATED = SkillFixture(
    id="repeated_values",
    files={
        "SKILL.md": lines(
            *FRONTMATTER,
            "Owner: jayant@company.com",
            "Escalate to jayant@company.com, then jayant@company.com again.",
        ),
        "scripts/a.sh": lines(f"export GITHUB_TOKEN={GITHUB_TOKEN}"),
        "scripts/b.sh": lines(f"export GITHUB_TOKEN={GITHUB_TOKEN}"),
    },
    expected=[
        Row("SKILL.md", 5, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant@company.com"),
        Row("SKILL.md", 6, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant@company.com"),
        Row("SKILL.md", 6, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant@company.com"),
        Row("scripts/a.sh", 1, C.CREDENTIAL, "github_token", 1.0, A.REPLACE, "GITHUB_TOKEN",
            "ghp_" + MASK),
        Row("scripts/b.sh", 1, C.CREDENTIAL, "github_token", 1.0, A.REPLACE, "GITHUB_TOKEN",
            "ghp_" + MASK),
    ],
    secrets=[GITHUB_TOKEN],
)

EXAMPLES = SkillFixture(
    id="ambiguous_and_example_values",
    files={
        "SKILL.md": lines(
            *FRONTMATTER,
            'Set `OPENAI_API_KEY=sk-...` or `api_key: "your-api-key-here"`.',
            "Email user@example.com, you@gmail.com, or bob@co.com.",
            f"AWS sample: {AWS_SAMPLE_KEY} and arn:aws:iam::123456789012:role/demo.",
            "Paths like /path/to/model.gguf, /home/user/project, and ./scripts/run.py.",
            "Call 555-0123. See https://example.com/docs or https://github.com/owner/repo.",
            "Cite [the source](https://github.com/vllm-project/vllm/blob/main/README.md).",
            "Install from https://github.com/vllm-project/vllm today.",
        ),
        "config/example.yaml": lines(
            'api_key: "<YOUR_API_KEY>"',
            'password: "changeme"',
            "project_id: {{PROJECT_ID}}",
            "endpoint: https://api.example.com/v1",
            "username: __token__",
            "method: POST",
            "repository: https://github.com/vllm-project/vllm",
        ),
    },
    expected=[
        Row("SKILL.md", 6, C.EMAIL, "sample_email", 0.5, A.REVIEW, "", "bob@co.com"),
        Row("SKILL.md", 10, C.REPOSITORY, "repository_reference", 0.3, A.REVIEW, "",
            "https://github.com/vllm-project/vllm"),
        Row("SKILL.md", 11, C.REPOSITORY, "repository_reference", 0.3, A.REVIEW, "",
            "https://github.com/vllm-project/vllm"),
        Row("config/example.yaml", 7, C.REPOSITORY, "repository_url", 0.6, A.REVIEW,
            "repository", "https://github.com/vllm-project/vllm"),
    ],
)

MALFORMED = SkillFixture(
    id="malformed_files",
    files={
        "SKILL.md": lines(
            "---",
            "name: broken",
            "description: frontmatter that never closes",
            "owner_email: jayant@company.com",
        ),
        "references/unclosed.md": lines("```bash", f"export GITHUB_TOKEN={GITHUB_TOKEN}"),
        "config/truncated.json": f'{{"api_key": "{GENERIC_SECRET}", "out": "/Users/jayant/out',
        "scripts/crlf.py": f'import os\r\nTOKEN = "{GITHUB_TOKEN}"\r\n',
        "scripts/bom.py": '\ufeffOWNER_EMAIL = "jayant@company.com"\n',
        "references/empty.md": "",
        "references/latin1.txt": "caf\u00e9 jayant@company.com\n".encode("latin-1"),
        "references/nul.txt": b"jayant@company.com\x00\x01\x02",
        "references/long.txt": "x " * 50_000 + "jayant@company.com\n",
    },
    expected=[
        Row("SKILL.md", 4, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant@company.com"),
        Row("config/truncated.json", 1, C.CREDENTIAL, "secret_assignment", 0.8, A.REPLACE,
            "api_key", MASK),
        Row("config/truncated.json", 1, C.FILE_PATH, "unix_home_path", 0.9, A.REPLACE, "",
            "/Users/jayant/out"),
        Row("references/long.txt", 1, C.EMAIL, "email", 0.9, A.REPLACE, "",
            "jayant@company.com"),
        Row("references/unclosed.md", 2, C.CREDENTIAL, "github_token", 1.0, A.REPLACE,
            "GITHUB_TOKEN", "ghp_" + MASK),
        Row("scripts/bom.py", 1, C.EMAIL, "email", 0.9, A.REPLACE, "OWNER_EMAIL",
            "jayant@company.com"),
        Row("scripts/crlf.py", 2, C.CREDENTIAL, "github_token", 1.0, A.REPLACE, "TOKEN",
            "ghp_" + MASK),
    ],
    skipped={
        "references/latin1.txt": SkipReason.NOT_UTF8,
        "references/nul.txt": SkipReason.BINARY,
    },
    secrets=[GITHUB_TOKEN, GENERIC_SECRET],
)

SYMLINKS = SkillFixture(
    id="symlinks",
    files={
        "SKILL.md": lines(*FRONTMATTER, "Nothing personal here."),
        "references/real.md": lines("Owner: jayant@company.com"),
    },
    symlinks={
        "references/link.md": "real.md",
        "linked_refs": "references",
        "secret.env": "outside:secret.env",
        "outside_dir": "outside:",
    },
    outside={"secret.env": lines(f"GITHUB_TOKEN={GITHUB_TOKEN}")},
    expected=[
        Row("references/real.md", 1, C.EMAIL, "email", 0.9, A.REPLACE, "", "jayant@company.com"),
    ],
    skipped={
        "linked_refs": SkipReason.SYMLINK,
        "outside_dir": SkipReason.SYMLINK,
        "references/link.md": SkipReason.SYMLINK,
        "secret.env": SkipReason.SYMLINK,
    },
    secrets=[GITHUB_TOKEN],
)

UNSUPPORTED = SkillFixture(
    id="unsupported_files",
    files={
        "SKILL.md": lines(*FRONTMATTER, "Nothing personal here."),
        "assets/logo.png": b"\x89PNG\r\n\x1a\n jayant@company.com",
        "docs/manual.pdf": b"%PDF-1.4 jayant@company.com",
        "LICENSE": lines("Copyright jayant@company.com"),
        "data/contacts.csv": lines("name,email", "Jayant,jayant@company.com"),
        "bin/deploy": lines("#!/bin/bash", f"export GITHUB_TOKEN={GITHUB_TOKEN}"),
        "node_modules/pkg/index.js": lines("// jayant@company.com"),
        ".git/config": lines("[remote]", "url = git@github.com:jayant/private.git"),
    },
    expected=[
        Row("bin/deploy", 2, C.CREDENTIAL, "github_token", 1.0, A.REPLACE, "GITHUB_TOKEN",
            "ghp_" + MASK),
    ],
    skipped={
        "LICENSE": SkipReason.UNSUPPORTED,
        "assets/logo.png": SkipReason.UNSUPPORTED,
        "data/contacts.csv": SkipReason.UNSUPPORTED,
        "docs/manual.pdf": SkipReason.UNSUPPORTED,
    },
    secrets=[GITHUB_TOKEN],
)

FIXTURES = [CLEAN, CREDENTIALS, EMAILS, PHONES, LOCAL_PATHS, ORGANIZATION, REPEATED,
            EXAMPLES, MALFORMED, SYMLINKS, UNSUPPORTED]
