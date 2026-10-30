import pytest

from skill_fixtures import FIXTURES, SkillFixture, materialize
from skill_fixtures import secrets as fake_secrets

SECRETS = [v for k, v in vars(fake_secrets).items() if k.isupper() and isinstance(v, str)]


def pytest_make_parametrize_id(config, val, argname):
    """Keep fake secrets out of test names, which pytest prints in its output."""
    if isinstance(val, str) and any(secret in val for secret in SECRETS):
        return f"{argname}-redacted"
    return None


@pytest.fixture(params=FIXTURES, ids=lambda f: f.id)
def skill_fixture(request) -> SkillFixture:
    return request.param


@pytest.fixture
def skill_dir(skill_fixture, tmp_path):
    return materialize(skill_fixture, tmp_path)
