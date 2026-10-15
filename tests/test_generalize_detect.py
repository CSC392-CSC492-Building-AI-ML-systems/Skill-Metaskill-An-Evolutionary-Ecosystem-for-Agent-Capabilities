"""Deterministic detectors in metaskill/generalize/detect.py.

Secret-shaped strings are assembled at runtime so this file contains none that a
repository secret scanner would flag.
"""

import json

import pytest

from metaskill.generalize.detect import (
    MASK,
    REDACTED,
    Category,
    Finding,
    detect_file,
    redact,
    scan_skill,
)
from metaskill.generalize.inventory import SourceFile

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6" * 3
GITHUB_PAT = "github_pat_" + "B" * 22 + "_" + "c" * 59
AWS_KEY = "AKIA" + "Z7Q2" * 4
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"
GOOGLE_KEY = "AIza" + "Sy" * 17 + "x"
SK_KEY = "sk-proj-" + "q9W8" * 6
STRIPE_KEY = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dozjgNryP4J3jVmNHl0w5N"
PRIVATE_KEY = ("-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nq83kd92\n"
               "-----END RSA " + "PRIVATE KEY-----")
GENERIC_SECRET = "abc123def456ghi789"


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
    (JWT, "jwt", "eyJ"),
])
def test_known_token_shapes(secret, rule, prefix):
    [finding] = scan("notes.txt", f"use {secret} here")
    assert finding.category is Category.CREDENTIAL
    assert finding.rule == rule
    assert finding.confidence == 1.0
    assert finding.value == prefix + MASK
    assert secret not in json.dumps(finding.to_dict())


def test_token_inside_longer_word_is_not_a_token():
    assert scan("notes.txt", "x" + GITHUB_TOKEN + "x") == []


@pytest.mark.parametrize("text", [
    'Authorization: "Bearer sk-' + "x" * 20 + '"',
    "aws_access_key_id = AKIA" + "IOSFODNN7EXAMPLE",  # AWS's documented sample key
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
    [finding] = scan("app.py", text)
    assert (finding.category, finding.rule, finding.value) == (
        Category.CREDENTIAL, "url_password", MASK)
    assert text[finding.start:finding.end] == "s3cr3tP4ss"
    assert finding.name == "db"


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
    ("config.yaml", "PROJECT_ID: my-gcp-project", "PROJECT_ID", "my-gcp-project"),
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
project_id: my-gcp-project
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
        (5, Category.CONFIGURATION, "project_id", "my-gcp-project"),
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
    report = json.dumps(result.to_dict())
    assert sum(f.category is Category.CREDENTIAL for f in result.findings) == 4
    for secret in secrets:
        assert secret not in report
        assert all(secret not in repr(f) for f in result.findings)


def test_finding_round_trips_through_dict():
    [finding] = scan("SKILL.md", "jayant@company.com")
    assert Finding.from_dict(json.loads(json.dumps(finding.to_dict()))) == finding


# --- Redaction ------------------------------------------------------------------

def test_redact_removes_credentials_and_keeps_line_numbers():
    text = f"one\nkey:\n{PRIVATE_KEY}\nTOKEN={GITHUB_TOKEN} by jayant@company.com\nlast"
    findings = scan("notes.txt", text)
    redacted = redact(text, findings)
    assert GITHUB_TOKEN not in redacted and "MIIEpAIBAAKCAQEA" not in redacted
    assert "jayant@company.com" in redacted  # only credentials by default
    assert redacted.count("\n") == text.count("\n")
    assert redacted.split("\n")[-1] == "last"
    assert redacted.count(REDACTED) == 2
