"""Fake credentials for detector tests.

Each one is assembled at runtime so no file in the repository contains a string that a
secret scanner would flag. None of them is a real credential.
"""

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6" * 3
GITHUB_PAT = ("github_pat_" + "11AJ3KQ0Y0aB3dE5gH7jK9" + "_"
              + "Zx81Qw7Er6Ty5Ui4Op3As2Df1Gh0Jk9Lm8Nbq8R9s0T1u2V3w4X5y6Z7aBc")
AWS_KEY = "AKIA" + "Z7Q2" * 4
AWS_SAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # AWS's documented example key
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"
GOOGLE_KEY = "AIza" + "Sy" * 17 + "x"
SK_KEY = "sk-proj-" + "q9W8" * 6
STRIPE_KEY = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
HF_TOKEN = "hf_" + "kQ7rT2mX9pL4vN8s" * 2
NPM_TOKEN = "npm_" + "Zx81Qw7Er6Ty5Ui4Op3As2Df1Gh0Jk9Lm8Nb"
GITLAB_TOKEN = "glpat-" + "x7Yb2Kq9Lm3Np8Rs4Tv5"
PYPI_TOKEN = "pypi-" + "AgEIcHlwaS5vcmc" + "CJGI2ZTk1NDI3LWQ3ZWQtNDU2" * 3
SENDGRID_KEY = "SG." + "aB3dE5gH7jK9mN1pQ3sT5v" + "." + "wX7yZ9" * 7 + "a"
SLACK_WEBHOOK = ("https://hooks.slack.com/" + "services/" + "T0A1B2C3D/B4E5F6G7H/"
                 + "q8R9s0T1u2V3w4X5y6Z7a8B9")
DISCORD_WEBHOOK = ("https://discord.com/api/" + "webhooks/" + "112233445566778899/"
                   + "Ab3De6Gh9Jk2Mn5Pq8St1Vw4Yz7Bc0Ef3Hi6Kl9No2")
JWT = ("eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "."
       + "dozjgNryP4J3jVmNHl0w5N")
PRIVATE_KEY = ("-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nq83kd92\n"
               "-----END RSA " + "PRIVATE KEY-----")
PRIVATE_KEY_BODY = "MIIEpAIBAAKCAQEA"
GENERIC_SECRET = "abc123def456ghi789"
DB_PASSWORD = "s3cr3tP4ss"


def assert_not_leaked(secrets, *blobs) -> None:
    """Fail if a secret appears in any blob, without printing the secret or the blob.

    A plain `assert secret not in blob` would show both in pytest's failure message.
    """
    for i, blob in enumerate(blobs):
        leaked = sum(secret in blob for secret in secrets)
        assert leaked == 0, f"{leaked} secret(s) found in output #{i}"
