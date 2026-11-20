import os
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

# Tests marked `llm` call a real model: they cost money, need a provider set up and are
# not deterministic, so normal runs and CI skip them (docs/generalize/classify.md).
LLM_ENV = "METASKILL_RUN_LLM"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"llm: calls a real LLM provider; set {LLM_ENV}=1 to include")


def pytest_collection_modifyitems(config, items):
    if os.environ.get(LLM_ENV) == "1":
        return
    skip_llm = pytest.mark.skip(reason=f"needs {LLM_ENV}=1 (calls a real LLM)")
    for item in items:
        if item.get_closest_marker("llm"):
            item.add_marker(skip_llm)


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
