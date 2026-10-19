"""Deterministic detectors in metaskill/generalize/detect.py.

Secret-shaped strings come from skill_fixtures.secrets, which assembles them at runtime
so no file contains one that a repository secret scanner would flag.
"""

import json

import pytest
from skill_fixtures.secrets import (
    AWS_KEY,
    DISCORD_WEBHOOK,
    GENERIC_SECRET,
    GITHUB_PAT,
    GITHUB_TOKEN,
    GITLAB_TOKEN,
    GOOGLE_KEY,
    HF_TOKEN,
    JWT,
    NPM_TOKEN,
    PRIVATE_KEY,
    PYPI_TOKEN,
    SENDGRID_KEY,
    SK_KEY,
    SLACK_TOKEN,
    SLACK_WEBHOOK,
    STRIPE_KEY,
    assert_not_leaked,
)

from metaskill.generalize.detect import (
    MASK,
    REDACTED,
    Action,
    Category,
    Finding,
    action_for,
    detect_file,
    redact,
    scan_skill,
)
from metaskill.generalize.inventory import SourceFile


def scan(path: str, text: str) -> list[Finding]:
    return detect_file(SourceFile(path, text))


def values(path: str, text: str, category: Category | None = None) -> list[str]:
    return [f.value for f in scan(path, text) if category is None or f.category is category]


def write(root, files: dict[str, str]):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --- Emails ---------------------------------------------------------------------

@pytest.mark.parametrize("email", [
    "jayant@gmail.com",
    "user@company.com",
    "first.last+tag@mail.company.co.uk",
])
def test_valid_emails(email):
    findings = scan("SKILL.md", f"Contact {email} for access.")
    assert [(f.category, f.value) for f in findings] == [(Category.EMAIL, email)]


@pytest.mark.parametrize("text", [
    "foo@bar",  # no top-level domain
    "@app.route('/')",
    "npm install left-pad@1.3.0",
    "user@example.com",  # reserved for documentation
    "ops@build.test",
    "name@domain.c",
    "@scope/package",
    'email = "you@gmail.com"',  # placeholder name
])
def test_email_like_strings_are_not_emails(text):
    assert values("SKILL.md", text, Category.EMAIL) == []


def test_email_at_end_of_sentence_excludes_period():
    assert values("SKILL.md", "Write to jayant@company.com.") == ["jayant@company.com"]


# --- Credentials ----------------------------------------------------------------

@pytest.mark.parametrize("secret, rule, prefix", [
    (GITHUB_TOKEN, "github_token", "ghp_"),
    (GITHUB_PAT, "github_fine_grained_token", "github_pat_"),
    (AWS_KEY, "aws_access_key_id", "AKIA"),
    (SLACK_TOKEN, "slack_token", "xoxb-"),
    (GOOGLE_KEY, "google_api_key", "AIza"),
    (SK_KEY, "sk_api_key", "sk-proj-"),
    (STRIPE_KEY, "stripe_secret_key", "sk_live_"),
    (HF_TOKEN, "huggingface_token", "hf_"),
    (NPM_TOKEN, "npm_token", "npm_"),
    (GITLAB_TOKEN, "gitlab_token", "glpat-"),
    (PYPI_TOKEN, "pypi_token", "pypi-"),
    (SENDGRID_KEY, "sendgrid_key", "SG."),
    (SLACK_WEBHOOK, "slack_webhook", "https://hooks.slack.com/services/"),
    (DISCORD_WEBHOOK, "discord_webhook", "https://discord.com/api/webhooks/"),
    (JWT, "jwt", "eyJ"),
])
def test_known_token_shapes(secret, rule, prefix):
    [finding] = scan("notes.txt", f"use {secret} here")
    assert finding.category is Category.CREDENTIAL
    assert finding.rule == rule
    assert finding.confidence == 1.0
    assert finding.action is Action.REPLACE
    assert finding.value == prefix + MASK
    assert_not_leaked([secret], json.dumps(finding.to_dict()), repr(finding))


def test_token_inside_longer_word_is_not_a_token():
    assert scan("notes.txt", "x" + GITHUB_TOKEN + "x") == []


@pytest.mark.parametrize("text", [
    'Authorization: "Bearer sk-' + "x" * 20 + '"',
    "aws_access_key_id = AKIA" + "IOSFODNN7EXAMPLE",  # AWS's documented sample key
    "hf_" + "x" * 34,
    "glpat-" + "x" * 20,
    "https://hooks.slack.com/" + "services/T00000000/B00000000/" + "X" * 24,
    "-----BEGIN RSA " + "PRIVATE KEY-----\n<your key here>\n-----END RSA " + "PRIVATE KEY-----",
])
def test_documentation_filler_tokens(text):
    assert values("SKILL.md", text, Category.CREDENTIAL) == []


def test_private_key_block_spans_lines_and_keeps_only_header():
    text = "key:\n" + PRIVATE_KEY + "\nend"
    [finding] = scan("notes.txt", text)
    assert finding.rule == "private_key"
    assert finding.line == 2
    assert text[finding.start:finding.end] == PRIVATE_KEY
    assert finding.value == "-----BEGIN RSA PRIVATE KEY-----" + MASK


def test_password_in_connection_string():
    text = 'db = "postgres://admin:s3cr3tP4ss@db.internal:5432/app"'
    password, host = scan("app.py", text)
    assert (password.category, password.rule, password.value) == (
        Category.CREDENTIAL, "url_password", MASK)
    assert text[password.start:password.end] == "s3cr3tP4ss"
    assert password.name == "db"
    assert (host.category, host.value) == (Category.ORGANIZATION_HOST, "db.internal")


@pytest.mark.parametrize("text", [
    "https://jayant@host.internal/path",  # user but no password
    "postgres://app:${DB_PASSWORD}@db/app",
    "postgres://user:password@localhost/db",
])
def test_connection_strings_without_a_real_password(text):
    assert values("SKILL.md", text, Category.CREDENTIAL) == []


@pytest.mark.parametrize("path, text, name", [
    (".env", f'API_KEY="{GENERIC_SECRET}"', "API_KEY"),
    ("run.sh", f"export SERVICE_TOKEN={GENERIC_SECRET}", "SERVICE_TOKEN"),
    ("config.yaml", f'api_key: "{GENERIC_SECRET}"', "api_key"),
    ("client.py", f'token = "{GENERIC_SECRET}"', "token"),
    ("client.js", f"const client = new Client({{ apiKey: '{GENERIC_SECRET}' }})", "apiKey"),
    ("settings.json", f'{{"password": "{GENERIC_SECRET}"}}', "password"),
    ("deploy.py", f'self.secret_key = "{GENERIC_SECRET}"', "self.secret_key"),
])
def test_secret_assignments(path, text, name):
    [finding] = scan(path, text)
    assert (finding.category, finding.rule, finding.name, finding.value) == (
        Category.CREDENTIAL, "secret_assignment", name, MASK)
    assert text[finding.start:finding.end] == GENERIC_SECRET


def test_known_token_in_assignment_is_one_finding_with_the_name():
    [finding] = scan("run.sh", f'GITHUB_TOKEN="{GITHUB_TOKEN}"')
    assert (finding.rule, finding.name) == ("github_token", "GITHUB_TOKEN")


@pytest.mark.parametrize("path, text", [
    ("client.py", "max_tokens = 4096"),
    ("client.py", 'token = os.environ["GITHUB_TOKEN"]'),  # a lookup, not a literal
    ("client.py", "token = get_token()"),
    ("config.yaml", "token: os.environ.get"),
    ("client.py", 'api_key = "your-api-key-here"'),
    ("client.py", 'password = "********"'),
    ("client.py", 'token = ""'),
    ("config.yaml", "password: ${DB_PASSWORD}"),
    ("config.yaml", "token: ${{ secrets.GITHUB_TOKEN }}"),
    ("client.py", 'api_key_env = "OPENAI_API_KEY"'),
    ("client.py", 'token_url = "https://auth.company.com/token"'),
    ("client.py", 'API_KEY = "OPENAI_API_KEY"'),  # names a variable
    ("client.py", 'token = "short"'),
    ("client.py", 'password = "correct horse battery staple"'),
    ("client.py", 'if token == "abcdefghijkl":'),
    ("SKILL.md", f"The token: {GENERIC_SECRET} is described in prose"),  # outside code
    ("run.sh", 'docker run -e JUPYTER_PASSWORD="mypassword"'),  # a word, not random
    ("client.py", 'llm = OpenAI(api_key="not-needed")'),
    ("config.json", '{"tokenField": "accessToken"}'),
    ("run.sh", 'API_KEY_SOURCE="credentials"'),
])
def test_not_secret_assignments(path, text):
    assert values(path, text, Category.CREDENTIAL) == []


# --- File paths -----------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Saved to /Users/jayant/projects/out.csv", "/Users/jayant/projects/out.csv"),
    ("cd /home/jayant/work && make", "/home/jayant/work"),
    ("Results go in /Users/jayant/out.", "/Users/jayant/out"),
    ("open('/home/jayant/data.json')", "/home/jayant/data.json"),
    ("home is /Users/jayant", "/Users/jayant"),
    ("file:///Users/jayant/report.html", "/Users/jayant/report.html"),
])
def test_unix_user_paths(text, expected):
    assert values("SKILL.md", text) == [expected]


@pytest.mark.parametrize("text, expected", [
    (r"Open C:\Users\Jayant\Documents\notes.txt now", r"C:\Users\Jayant\Documents\notes.txt"),
    (r'{"out": "C:\\Users\\Jayant\\out"}', r"C:\\Users\\Jayant\\out"),
    ("D:/Users/jayant/repo", "D:/Users/jayant/repo"),
    (r"See c:\users\jayant.", r"c:\users\jayant"),
])
def test_windows_user_paths(text, expected):
    found = scan("notes.json", text)
    assert [(f.category, f.value) for f in found] == [(Category.FILE_PATH, expected)]


@pytest.mark.parametrize("text", [
    "python ./scripts/validate.py",
    "python scripts/validate.py",
    "see ../references/api.md",
    "python ${HERMES_SKILL_DIR}/scripts/upload.py",
    "ls ~/.hermes/skills",
    "write to /tmp/output.json",
    "/usr/local/bin/python3",
    "/home/user/project",  # documentation username
    "cp -r data /home/ubuntu/dataset",  # cloud image default user
    r"C:\Users\Public\Desktop",
    "https://company.com/home/jayant",  # URL path, not a file path
    "/Users/{{USERNAME}}/out",
])
def test_paths_that_are_not_user_specific(text):
    assert values("SKILL.md", text, Category.FILE_PATH) == []


def test_relative_path_assignment_is_not_configuration():
    assert scan("config.yaml", "output_dir: ./out\nscript_path: scripts/validate.py") == []


# --- Repositories ---------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Clone https://github.com/jayant/my-repo", "https://github.com/jayant/my-repo"),
    ("see github.com/company/internal-repo for details", "github.com/company/internal-repo"),
    ("git clone git@github.com:jayant/my-repo.git", "git@github.com:jayant/my-repo.git"),
    ("https://gitlab.com/team/service.", "https://gitlab.com/team/service"),
    ("https://github.com/jayant/my-repo/blob/main/x.py", "https://github.com/jayant/my-repo"),
])
def test_repository_urls(text, expected):
    found = scan("SKILL.md", text)
    assert [(f.category, f.value) for f in found] == [(Category.REPOSITORY, expected)]
    assert found[0].confidence < 1.0  # may be public; left for review


@pytest.mark.parametrize("text", [
    "Create a token at https://github.com/settings/tokens",
    "https://github.com/owner/repo",
    "https://api.github.com/repos/jayant/my-repo",
    "https://docs.github.com/en/rest",
    "https://github.com/jayant",  # a profile, no repository
])
def test_github_urls_that_are_not_repositories(text):
    assert values("SKILL.md", text, Category.REPOSITORY) == []


# --- Environment variables and configuration ------------------------------------

@pytest.mark.parametrize("path, text, name", [
    ("app.py", 'os.getenv("GITHUB_TOKEN")', "GITHUB_TOKEN"),
    ("app.py", "os.environ['API_KEY']", "API_KEY"),
    ("app.py", 'os.environ.get("PROJECT_ID", "")', "PROJECT_ID"),
    ("app.js", "const out = process.env.OUTPUT_PATH;", "OUTPUT_PATH"),
    ("app.ts", "process.env['REPOSITORY']", "REPOSITORY"),
    ("run.sh", 'curl -H "Authorization: $GITHUB_TOKEN"', "GITHUB_TOKEN"),
    ("run.sh", "gcloud config set project ${PROJECT_ID:-default}", "PROJECT_ID"),
])
def test_environment_variable_references(path, text, name):
    [finding] = scan(path, text)
    assert (finding.category, finding.value, finding.name) == (
        Category.ENVIRONMENT_VARIABLE, name, name)


def test_builtin_variables_are_not_configuration():
    assert scan("run.sh", 'cd "$HOME" && echo $PATH ${HERMES_SKILL_DIR}/run') == []


def test_shell_locals_are_not_environment_variables():
    text = ('PORT=8080\nlocal RESPONSE=""\nfor FILE in *.md; do echo $FILE; done\n'
            'read -r ANSWER\necho "$PORT $RESPONSE $ANSWER $GITHUB_TOKEN"\n')
    assert values("run.sh", text, Category.ENVIRONMENT_VARIABLE) == ["GITHUB_TOKEN"]


@pytest.mark.parametrize("path, text, name, value", [
    ("run.sh", "export OUTPUT_PATH=/data/out", "OUTPUT_PATH", "/data/out"),
    ("config.yaml", "PROJECT_ID: atlas-prod-421", "PROJECT_ID", "atlas-prod-421"),
    ("app.py", 'REPOSITORY = "jayant/my-repo"', "REPOSITORY", "jayant/my-repo"),
    ("settings.toml", 'region = "us-east-1"', "region", "us-east-1"),
    (".env", "LOG_LEVEL=debug", "LOG_LEVEL", "debug"),  # every .env entry is configuration
])
def test_configuration_assignments(path, text, name, value):
    [finding] = scan(path, text)
    assert (finding.category, finding.name, finding.value) == (
        Category.CONFIGURATION, name, value)


def test_specific_value_keeps_the_configuration_name():
    findings = scan("run.sh",
                    "OUTPUT_PATH=/Users/jayant/out\nREPOSITORY=https://github.com/jayant/x")
    assert [(f.category, f.name) for f in findings] == [
        (Category.FILE_PATH, "OUTPUT_PATH"), (Category.REPOSITORY, "REPOSITORY")]


def test_reference_and_value_are_told_apart():
    findings = scan("run.sh", "export OUTPUT_PATH=/data/out\ncp result $OUTPUT_PATH")
    assert [(f.category, f.value) for f in findings] == [
        (Category.CONFIGURATION, "/data/out"),
        (Category.ENVIRONMENT_VARIABLE, "OUTPUT_PATH"),
    ]


@pytest.mark.parametrize("path, text", [
    ("run.sh", "LOG_LEVEL=debug"),  # not a configuration-like name outside .env
    ("app.py", "project_dir = Path(__file__).parent"),
    ("config.yaml", "workspace: ${WORKSPACE}"),
    ("SKILL.md", "Repository: see the docs below."),  # prose, not an assignment
    ("config.yaml", "host: localhost"),
    ("config.yaml", "OP_CONNECT_HOST: http://localhost:8080"),
    ("schema.yaml", "path: str"),  # a type, not a value
    ("config.json", '{"snapshotDir": "__snapshots__"}'),  # relative location
    ("config.yaml", "path: Intel/orca_dpo_pairs"),
    ("config.yaml", "description: Posts updates. Channel-gated: this skill only replies"),
    ("app.py", 'lm += "Email: " + gen("email")'),
    ("ds_config.yaml", "allgather_bucket_size: 5e8"),  # a setting about buckets
    ("run.sh", "export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH"),
    ("config.yaml", "model_path: /path/to/model.gguf"),
])
def test_not_configuration(path, text):
    assert values(path, text, Category.CONFIGURATION) == []


# --- Markdown -------------------------------------------------------------------

SKILL_MD = """---
name: gcp-file-transfer
description: Move files to Cloud Storage
version: 1.0.0
project_id: atlas-prod-421
---
# GCP file transfer

Review pull requests for security vulnerabilities.
Review pull requests from the Atlas Streams repository.
Repository: see below.

```bash
export BUCKET=atlas-prod-uploads
```

```python
bucket = os.environ["BUCKET"]
```
"""


def test_markdown_assignments_only_in_frontmatter_and_code_blocks():
    findings = scan("SKILL.md", SKILL_MD)
    assert [(f.line, f.category, f.name, f.value) for f in findings] == [
        (5, Category.CONFIGURATION, "project_id", "atlas-prod-421"),
        (14, Category.CONFIGURATION, "BUCKET", "atlas-prod-uploads"),
        (18, Category.ENVIRONMENT_VARIABLE, "BUCKET", "BUCKET"),
    ]


def test_organization_names_in_prose_are_left_to_the_classifier():
    text = ("Review pull requests for security vulnerabilities.\n"
            "Review pull requests from the Atlas Streams repository.\n")
    assert scan("SKILL.md", text) == []


# --- Whole files ----------------------------------------------------------------

def test_multiple_detections_in_one_file_in_order():
    text = (
        "# Owner: jayant@company.com\n"
        f'TOKEN = "{GITHUB_TOKEN}"\n'
        'OUTPUT_PATH = "/Users/jayant/out"\n'
        'REPO_URL = "https://github.com/jayant/my-repo"\n'
        'key = os.getenv("PROJECT_ID")\n'
    )
    findings = scan("scripts/upload.py", text)
    assert [(f.line, f.column, f.category) for f in findings] == [
        (1, 10, Category.EMAIL),
        (2, 10, Category.CREDENTIAL),
        (3, 16, Category.FILE_PATH),
        (4, 13, Category.REPOSITORY),
        (5, 18, Category.ENVIRONMENT_VARIABLE),
    ]
    for f in findings:
        if not f.sensitive:
            assert text[f.start:f.end] == f.value


def test_detections_across_file_types(tmp_path):
    write(tmp_path, {
        "SKILL.md": "---\nname: s\ndescription: d\n---\nAsk jayant@company.com\n",
        "scripts/upload.py": 'os.getenv("API_KEY")\n',
        "scripts/client.js": f"const t = '{SLACK_TOKEN}';\n",
        "references/settings.json": '{"out": "/home/jayant/out"}\n',
        "templates/config.yaml": "repository: github.com/company/internal-repo\n",
        "scripts/setup.sh": f"export AWS_ACCESS_KEY_ID={AWS_KEY}\n",
        ".env": "PROJECT_ID=atlas-prod\n",
        "Dockerfile": f"ENV DEPLOY_TOKEN={GENERIC_SECRET}\n",
    })
    result = scan_skill(tmp_path)
    assert {(f.file, f.category) for f in result.findings} == {
        ("SKILL.md", Category.EMAIL),
        ("scripts/upload.py", Category.ENVIRONMENT_VARIABLE),
        ("scripts/client.js", Category.CREDENTIAL),
        ("references/settings.json", Category.FILE_PATH),
        ("templates/config.yaml", Category.REPOSITORY),
        ("scripts/setup.sh", Category.CREDENTIAL),
        (".env", Category.CONFIGURATION),
        ("Dockerfile", Category.CREDENTIAL),
    }


def test_clean_skill_has_no_findings(tmp_path):
    write(tmp_path, {
        "SKILL.md": (
            "---\nname: pr-review\ndescription: Review pull requests\nversion: 1.0.0\n"
            "metadata:\n  hermes:\n    config:\n      - key: review.repo\n"
            "        description: Repository to review\n"
            "required_environment_variables:\n  - name: GITHUB_TOKEN\n"
            "    prompt: Enter your GitHub token\n---\n"
            "Review pull requests for security vulnerabilities.\n"
            "Run `python ${HERMES_SKILL_DIR}/scripts/review.py` or ./scripts/review.py.\n"
            "Docs: https://hermes-agent.nousresearch.com/docs and user@example.com.\n"
        ),
        "scripts/review.py": "import sys\nprint(sys.argv)\n",
    })
    assert scan_skill(tmp_path).findings == []


def test_scan_does_not_modify_files(tmp_path):
    write(tmp_path, {
        "SKILL.md": f"jayant@company.com\nexport GITHUB_TOKEN={GITHUB_TOKEN}\n",
        "scripts/run.py": 'API_KEY = "' + GENERIC_SECRET + '"\r\nprint(1)\r\n',
    })
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    result = scan_skill(tmp_path)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert result.findings
    assert after == before


def test_offsets_index_the_file_as_read_including_crlf(tmp_path):
    text = 'print(1)\r\nAPI_KEY = "' + GENERIC_SECRET + '"\r\n'
    (tmp_path / "run.py").write_bytes(text.encode())
    [finding] = scan_skill(tmp_path).findings
    assert finding.line == 2
    assert text[finding.start:finding.end] == GENERIC_SECRET


def test_scans_are_deterministic(tmp_path):
    write(tmp_path, {"SKILL.md": "jayant@company.com /Users/jayant/x\n",
                     "b.py": f'token = "{GENERIC_SECRET}"\n'})
    first, second = scan_skill(tmp_path), scan_skill(tmp_path)
    assert first.to_dict() == second.to_dict()
    assert len({f.id for f in first.findings}) == len(first.findings)


def test_report_never_contains_secrets_or_file_contents(tmp_path):
    secrets = [GITHUB_TOKEN, AWS_KEY, GENERIC_SECRET, "s3cr3tP4ss"]
    write(tmp_path, {
        "SKILL.md": f"export GITHUB_TOKEN={GITHUB_TOKEN}\n",
        "run.sh": f"AWS_KEY={AWS_KEY}\nexport DB_PASSWORD={GENERIC_SECRET}\n",
        "db.py": 'URL = "postgres://admin:s3cr3tP4ss@db/app"\n',
    })
    result = scan_skill(tmp_path)
    assert sum(f.category is Category.CREDENTIAL for f in result.findings) == 4
    assert_not_leaked(secrets, json.dumps(result.to_dict()),
                      *(repr(f) for f in result.findings))


def test_finding_round_trips_through_dict():
    [finding] = scan("SKILL.md", "jayant@company.com")
    assert finding.to_dict()["action"] == "replace"
    assert Finding.from_dict(json.loads(json.dumps(finding.to_dict()))) == finding


def test_finding_from_dict_without_action_derives_it():
    [finding] = scan("SKILL.md", "privacy@company.com")
    data = finding.to_dict()
    del data["action"]
    assert Finding.from_dict(data).action is Action.REVIEW


# --- Actions --------------------------------------------------------------------

@pytest.mark.parametrize("category, confidence, action", [
    (Category.ENVIRONMENT_VARIABLE, 1.0, Action.KEEP),  # a reference, not a value
    (Category.CREDENTIAL, 0.8, Action.REPLACE),  # always removed, whatever the confidence
    (Category.EMAIL, 0.9, Action.REPLACE),
    (Category.PHONE, 0.9, Action.REPLACE),
    (Category.FILE_PATH, 0.9, Action.REPLACE),
    (Category.EMAIL, 0.5, Action.REVIEW),
    (Category.CONFIGURATION, 0.8, Action.REVIEW),
    (Category.ORGANIZATION_HOST, 0.7, Action.REVIEW),
    (Category.REPOSITORY, 0.3, Action.REVIEW),
])
def test_action_for(category, confidence, action):
    assert action_for(category, confidence) is action


def test_every_finding_carries_the_action_for_its_category_and_confidence():
    text = (f'TOKEN = "{GITHUB_TOKEN}"\nowner = "jayant@company.com"\n'
            'team = "atlas-streams"\nkey = os.getenv("PROJECT_ID")\n')
    findings = scan("app.py", text)
    assert [f.action for f in findings] == [
        Action.REPLACE, Action.REPLACE, Action.REVIEW, Action.KEEP]
    assert all(f.action is action_for(f.category, f.confidence) for f in findings)


# --- Redaction ------------------------------------------------------------------

def test_redact_removes_credentials_and_keeps_line_numbers():
    text = f"one\nkey:\n{PRIVATE_KEY}\nTOKEN={GITHUB_TOKEN} by jayant@company.com\nlast"
    findings = scan("notes.txt", text)
    redacted = redact(text, findings)
    assert_not_leaked([GITHUB_TOKEN, "MIIEpAIBAAKCAQEA"], redacted)
    assert "jayant@company.com" in redacted  # only credentials by default
    assert redacted.count("\n") == text.count("\n")
    assert redacted.split("\n")[-1] == "last"
    assert redacted.count(REDACTED) == 2


# --- Phone numbers --------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Call +1 (416) 978-2011 today", "+1 (416) 978-2011"),
    ("Call 416-978-2011.", "416-978-2011"),
    ("Call (416) 978-2011", "(416) 978-2011"),
    ("Call 416.978.2011", "416.978.2011"),
    ("Call 1-416-978-2011", "1-416-978-2011"),
    ("Call +14169782011", "+14169782011"),  # E.164
    ("Call +44 20 8642 9753", "+44 20 8642 9753"),
    ("Call +91-98765-43210", "+91-98765-43210"),
])
def test_phone_numbers(text, expected):
    [finding] = scan("SKILL.md", text)
    assert (finding.category, finding.rule, finding.confidence, finding.action,
            finding.value) == (Category.PHONE, "phone_number", 0.9, Action.REPLACE, expected)
    assert text[finding.start:finding.end] == expected


@pytest.mark.parametrize("text", ["Support: 1-800-266-8228", "Support: +1 888 245 1655"])
def test_toll_free_numbers_are_left_for_review(text):
    [finding] = scan("SKILL.md", text)
    assert (finding.rule, finding.confidence, finding.action) == (
        "toll_free_phone", 0.5, Action.REVIEW)


@pytest.mark.parametrize("text", [
    "Call 555-0123",  # seven digits
    "Call (555) 555-1234",  # reserved for fiction
    "Call 416-555-0199",
    "Call +1 555 555 5555",
    "id 123-456-7890",  # an area code cannot start with 1
    "Call 416-078-2011",  # nor an exchange with 0
    "Call 000-000-0000",
    "Call +1 234 567 8901",  # sequential digits
    "released 2026-10-17",
    "version 1.2.3.4 and 10.20.300.4000",
    "ISBN 978-0-306-40615-7",
    "coefficient +4.0767416621",
    "build 416-978-2011-rc1",
    "order 416-978-20111",
    "x +2025",
    "uuid 550e8400-e29b-41d4-a716-446655440000",
    "timestamp 1697558400.123",
])
def test_not_phone_numbers(text):
    assert values("SKILL.md", text, Category.PHONE) == []


def test_phone_number_takes_the_configuration_name():
    [finding] = scan("run.sh", 'ONCALL_PHONE="+1 416 978 2011"')
    assert (finding.category, finding.name) == (Category.PHONE, "ONCALL_PHONE")


# --- Organization hosts ---------------------------------------------------------

@pytest.mark.parametrize("text, rule, confidence, expected", [
    ("https://grafana.corp/d/incidents", "internal_host", 0.7, "grafana.corp"),
    ("ssh build01.lan", "internal_host", 0.7, "build01.lan"),
    ("call api.platform.internal", "internal_host", 0.7, "api.platform.internal"),
    ("open http://nas.local/admin", "internal_host", 0.7, "nas.local"),
    ("print to printer.local:631", "internal_host", 0.7, "printer.local"),
    ("https://atlas-streams.atlassian.net/jira", "saas_tenant_host", 0.7,
     "atlas-streams.atlassian.net"),
    ("join atlas-eng.slack.com", "saas_tenant_host", 0.7, "atlas-eng.slack.com"),
    ("docs at https://nvidia.github.io/TensorRT-LLM/", "pages_host", 0.3, "nvidia.github.io"),
    ("staging 10.20.30.40", "private_ip", 0.5, "10.20.30.40"),
    ("staging 172.16.5.4", "private_ip", 0.5, "172.16.5.4"),
    ("staging 192.168.7.23", "private_ip", 0.5, "192.168.7.23"),
])
def test_organization_hosts(text, rule, confidence, expected):
    [finding] = scan("SKILL.md", text)
    assert (finding.category, finding.rule, finding.confidence, finding.action,
            finding.value) == (Category.ORGANIZATION_HOST, rule, confidence, Action.REVIEW,
                               expected)


@pytest.mark.parametrize("text", [
    "curl http://host.docker.internal:8080",  # the same on every Docker install
    "curl http://metadata.google.internal/computeMetadata",
    "see README.local for details",  # .local alone is too common a suffix
    "https://www.slack.com and https://api.slack.com and https://app.slack.com",
    "https://docs.github.io",
    "router at 192.168.1.1",  # gateway addresses are the same everywhere
    "allow 10.0.0.0/8",  # a range, not a host
    "broadcast 10.0.0.255",
    "bad 192.168.300.4",
    "public 8.8.8.8",
])
def test_not_organization_hosts(text):
    assert values("SKILL.md", text, Category.ORGANIZATION_HOST) == []


def test_aws_account_id_in_arn():
    text = "aws_role: arn:aws:iam::493817261504:role/triage"
    [finding] = scan("config.yaml", text)
    assert (finding.category, finding.rule, finding.confidence, finding.action,
            finding.value) == (Category.CONFIGURATION, "aws_account_id", 0.8, Action.REVIEW,
                               "493817261504")


@pytest.mark.parametrize("account", ["123456789012", "111122223333", "111111111111"])
def test_sample_aws_accounts_are_ignored(account):
    assert scan("config.yaml", f"role: arn:aws:iam::{account}:role/demo") == []


def test_host_takes_the_configuration_name():
    [finding] = scan("config.yaml", "db_host: db.internal")
    assert (finding.rule, finding.name) == ("internal_host", "db_host")


# --- Email and repository tiers -------------------------------------------------

@pytest.mark.parametrize("path, text, rule", [
    ("SKILL.md", "Write to privacy@company.com", "role_email"),
    ("SKILL.md", "Invite alice@co.com", "sample_email"),
    ("SKILL.md", "Email: john.smith@email.com", "sample_email"),
    ("SKILL.md", "Run `ssh deploy@build.company.com`", "ssh_destination"),
    ("run.sh", "ssh -p 443 \\\n  -R0:localhost:8000 free@a.pinggy.io", "ssh_destination"),
])
def test_emails_that_are_probably_not_personal_are_left_for_review(path, text, rule):
    [finding] = scan(path, text)
    assert (finding.category, finding.rule, finding.confidence, finding.action) == (
        Category.EMAIL, rule, 0.5, Action.REVIEW)


@pytest.mark.parametrize("path, text", [
    ("SKILL.md", "See [the repo](https://github.com/jayant/my-repo)."),
    ("SKILL.md", "See <https://github.com/jayant/my-repo>."),
    ("SKILL.md", "Clone https://github.com/jayant/my-repo first."),  # markdown prose
    ("notes.txt", "Bug: https://github.com/jayant/my-repo/issues/4"),
    ("run.py", "# see https://github.com/jayant/my-repo/blob/main/x.py"),
])
def test_cited_repositories_are_references(path, text):
    [finding] = scan(path, text)
    assert (finding.rule, finding.confidence, finding.action) == (
        "repository_reference", 0.3, Action.REVIEW)


@pytest.mark.parametrize("path, text", [
    ("SKILL.md", "```bash\ngit clone https://github.com/jayant/my-repo\n```"),
    ("run.sh", "git clone https://github.com/jayant/my-repo"),
    ("run.sh", "pip install git+https://github.com/jayant/my-repo"),
])
def test_repositories_used_in_commands_are_usages(path, text):
    [finding] = scan(path, text)
    assert (finding.rule, finding.confidence, finding.action) == (
        "repository_url", 0.6, Action.REVIEW)


# --- Environment variable and configuration tuning ------------------------------

def test_single_quoted_shell_text_is_not_expanded():
    text = "echo 'costs $PRICE'\necho \"$API_HOST\""
    assert values("run.sh", text, Category.ENVIRONMENT_VARIABLE) == ["API_HOST"]


def test_single_quotes_only_matter_in_shell():
    text = "```python\nprint('$API_HOST')\n```\n```bash\necho '$API_HOST'\n```"
    assert values("SKILL.md", text, Category.ENVIRONMENT_VARIABLE) == ["API_HOST"]


@pytest.mark.parametrize("text", [
    "COUNT=0; NAME=x; echo $COUNT $NAME",
    "if true; then STATUS=ok; fi; echo $STATUS",
    "for i in 1 2; do LAST=$i; done; echo $LAST",
    "(cd /tmp && TMP_OUT=1) && echo ${TMP_OUT}",
])
def test_shell_locals_after_separators(text):
    assert values("run.sh", text, Category.ENVIRONMENT_VARIABLE) == []


def test_runtime_and_tool_builtins_are_not_configuration():
    text = ("echo $CUDA_VISIBLE_DEVICES $WORLD_SIZE $MASTER_ADDR $GITHUB_WORKSPACE "
            "$XDG_CONFIG_HOME $CONDA_PREFIX $VIRTUAL_ENV $HERMES_HOME")
    assert scan("run.sh", text) == []


@pytest.mark.parametrize("path, text", [
    ("config.yaml", "username: __token__"),
    ("config.yaml", "method: POST"),
    ("config.yaml", 'query: "{QUERY}"'),
    ("config.yaml", "project: my-project"),
    ("config.yaml", "database_url: postgres://app@localhost:5432/app"),
    ("config.yaml", "output_path: results"),  # a path word needs an absolute value
    ("ssh_config", "UserKnownHostsFile=/dev/null"),
    ("client.py", 'headers = {"User-Agent": "hermes-agent/1.0"}'),
    ("config.yaml", "owner: me"),  # too short to identify anyone
])
def test_more_values_that_are_not_configuration(path, text):
    assert values(path, text, Category.CONFIGURATION) == []


# --- Overlapping detections -----------------------------------------------------

def test_email_wins_over_the_host_inside_it():
    [finding] = scan("SKILL.md", "Mail jayant@build.corp")
    assert (finding.category, finding.value) == (Category.EMAIL, "jayant@build.corp")


def test_credential_wins_over_configuration():
    [finding] = scan("config.yaml", f"api_key: {GITHUB_TOKEN}")
    assert (finding.category, finding.rule, finding.name) == (
        Category.CREDENTIAL, "github_token", "api_key")


def test_overlaps_resolve_the_same_way_every_time():
    text = (f'db = "postgres://admin:s3cr3tP4ss@db.internal:5432/app"\n'
            f'owner = "jayant@build.corp"\nREPO = "https://github.com/jayant/x"\n')
    runs = [[(f.start, f.end, f.rule, f.name) for f in scan("app.py", text)] for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    spans = [(start, end) for start, end, _, _ in runs[0]]
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
