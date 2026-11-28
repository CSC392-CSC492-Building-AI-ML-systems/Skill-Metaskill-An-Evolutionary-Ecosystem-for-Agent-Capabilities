import sys
from pathlib import Path

import pytest

# Lets any test import the shared helpers in tests/, e.g. `from hermes_skills import load`.
sys.path.insert(0, str(Path(__file__).parent))

# skill_fixtures lives in tests/, so it is importable only after the path insert above.
from skill_fixtures import FIXTURES, SkillFixture, materialize
from skill_fixtures import secrets as fake_secrets

# Fixture folders are test data, not tests: some Hermes skills ship their own test files.
collect_ignore_glob = ["fixtures/*", "*/fixtures/*"]

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


@pytest.fixture(autouse=True)
def _no_host_memory_reading(monkeypatch):
    """Tests see no host memory reading, so how many sessions run at once does not depend
    on the machine running them. tests/test_host.py sets its own readings."""
    from metaskill.evaluate import host
    monkeypatch.setattr(host, "available_memory", lambda: None)


@pytest.fixture(autouse=True)
def _own_saved_settings(monkeypatch, tmp_path_factory):
    """Each test gets its own, empty saved settings (/metaskill settings), never the
    settings of whoever runs the tests. tests/test_settings.py writes to it."""
    from metaskill.evaluate import settings
    path = tmp_path_factory.mktemp("settings") / "evaluate.json"
    monkeypatch.setattr(settings, "settings_file", lambda: path)
    return path
