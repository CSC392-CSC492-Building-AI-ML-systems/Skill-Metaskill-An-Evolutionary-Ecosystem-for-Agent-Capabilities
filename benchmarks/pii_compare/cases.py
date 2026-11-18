"""Labeled corpus for comparing PII detectors on skill-like files.

Every personal value below is fake. Each case is a small file plus:

  gold   - values that SHOULD be flagged, as (group, value). Each value must occur
           exactly once in the file so its span is unambiguous.
  decoys - values that look sensitive but should NOT be flagged (placeholders,
           documentation addresses, version numbers, package paths, ...).

Groups let the report show where each detector is strong:
  structured - emails, phones, credentials, IPs/hosts, home paths, repos
  freeform   - names, street addresses, dates of birth, card numbers
"""

from __future__ import annotations

CASES = [
    {
        "id": "contact-block",
        "file": "SKILL.md",
        "text": """\
# Onboarding helper

Contact Priya Nair at priya.nair@acme-corp.com or +44 7700 900123 if the steps fail.
Her office is at 12 Baker Street, London, and she was born on 4 March 1990.
Billing card on file: 4111 1111 1111 1111.
""",
        "gold": [
            ("freeform", "Priya Nair"),
            ("structured", "priya.nair@acme-corp.com"),
            ("structured", "+44 7700 900123"),
            ("freeform", "12 Baker Street, London"),
            ("freeform", "4 March 1990"),
            ("freeform", "4111 1111 1111 1111"),
        ],
        "decoys": [],
    },
    {
        "id": "deploy-script",
        "file": "deploy.sh",
        "text": """\
#!/bin/bash
# Deploy to staging, owner: Marcus Webb
export API_KEY=sk-proj-AbCdEf1234567890AbCdEf1234567890
ssh marcus@build.acme-corp.internal
scp ./dist deploy@10.20.30.40:/srv/app
echo "done"
""",
        "gold": [
            ("freeform", "Marcus Webb"),
            ("structured", "sk-proj-AbCdEf1234567890AbCdEf1234567890"),
            ("structured", "build.acme-corp.internal"),
            ("structured", "10.20.30.40"),
        ],
        "decoys": [],
    },
    {
        "id": "home-paths",
        "file": "run.py",
        "text": """\
import os

OUTPUT = "/Users/janedoe/projects/report-tool/out"
CACHE = r"C:\\Users\\tsmith\\AppData\\cache"
TOKEN = os.environ["REPORT_TOKEN"]
""",
        "gold": [
            ("structured", "/Users/janedoe/projects/report-tool/out"),
            ("structured", "C:\\Users\\tsmith\\AppData\\cache"),
        ],
        "decoys": [],
    },
    {
        "id": "placeholders-only",
        "file": "SKILL.md",
        "text": """\
# Setup

Set EMAIL to <your-email> and HOST to your-host.example.com.
Send questions to support@example.com or call 555-0100.
Run against 192.168.1.1 or 127.0.0.1 locally.
Example key format: sk-XXXXXXXXXXXXXXXXXXXXXXXX
""",
        "gold": [],
        "decoys": [
            "<your-email>",
            "your-host.example.com",
            "support@example.com",
            "555-0100",
            "192.168.1.1",
            "127.0.0.1",
        ],
    },
    {
        "id": "dev-noise",
        "file": "SKILL.md",
        "text": """\
# Python notes

Install sk-learn-compatible-estimators-package now, then run version 3.11.4.2 of the tool.
from google.protobuf.internal import decoder
The hash is 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08.
See section 4.1.2 and call get_user_name() to read the profile.
""",
        "gold": [],
        "decoys": [
            "sk-learn-compatible-estimators-package",
            "3.11.4.2",
            "google.protobuf.internal",
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        ],
    },
    {
        "id": "support-runbook",
        "file": "SKILL.md",
        "text": """\
---
name: support-runbook
owner: acme-team
---
# Support runbook

Escalate to Daniel Okafor (daniel.okafor@globex-inc.com, 416-978-2011).
Weekend on-call: Mei Lin Tan, reachable at (647) 931-4472.
Tenant console: https://globex.atlassian.net/wiki
""",
        "gold": [
            ("freeform", "Daniel Okafor"),
            ("structured", "daniel.okafor@globex-inc.com"),
            ("structured", "416-978-2011"),
            ("freeform", "Mei Lin Tan"),
            ("structured", "(647) 931-4472"),
            ("structured", "acme-team"),
            ("structured", "globex.atlassian.net"),
        ],
        "decoys": [],
    },
    {
        "id": "crlf-notes",
        "file": "SKILL.md",
        "text": "# Notes\r\n\r\nSend the invoice to Elena Petrova at elena.petrova@initech.io.\r\n"
                "Her mailing address is 221 Maple Avenue, Toronto, ON.\r\n"
                "```bash\r\nssh build@ci.initech.io \\\r\n  -p 22 deploy@prod.initech.io\r\n```\r\n",
        "gold": [
            ("freeform", "Elena Petrova"),
            ("structured", "elena.petrova@initech.io"),
            ("freeform", "221 Maple Avenue, Toronto, ON"),
            ("structured", "build@ci.initech.io"),
            ("structured", "deploy@prod.initech.io"),
        ],
        "decoys": [],
    },
    {
        "id": "config-yaml",
        "file": "config.yaml",
        "text": """\
owner_email: samir.rao@hooli-labs.com
api_token: ghp_AbCdEf1234567890AbCdEf1234567890ABCD
db_host: db-primary.hooli-labs.internal
timeout_seconds: 30
region: us-east-1
""",
        "gold": [
            ("structured", "samir.rao@hooli-labs.com"),
            ("structured", "ghp_AbCdEf1234567890AbCdEf1234567890ABCD"),
            ("structured", "db-primary.hooli-labs.internal"),
        ],
        "decoys": ["us-east-1"],
    },
    {
        "id": "narrative-pii",
        "file": "SKILL.md",
        "text": """\
# Customer reply template

When Olivia Hartman writes in about order 88231, confirm her shipping address,
902 Willow Court, Austin, TX 78701, and note that her date of birth is 07/15/1988
for the verification step. Her social security number 078-05-1120 must never be echoed.
""",
        "gold": [
            ("freeform", "Olivia Hartman"),
            ("freeform", "902 Willow Court, Austin, TX 78701"),
            ("freeform", "07/15/1988"),
            ("freeform", "078-05-1120"),
        ],
        "decoys": ["88231"],
    },
    {
        "id": "repo-and-aws",
        "file": "SKILL.md",
        "text": """\
# Release steps

Clone https://github.com/globex-corp/private-tools and set AWS_ACCESS_KEY_ID=AKIAJ3XQ7ZP2MNB5TRVA.
Then ask Tomasz Kowalski to approve the release.
""",
        "gold": [
            ("structured", "https://github.com/globex-corp/private-tools"),
            ("structured", "AKIAJ3XQ7ZP2MNB5TRVA"),
            ("freeform", "Tomasz Kowalski"),
        ],
        "decoys": [],
    },
]
