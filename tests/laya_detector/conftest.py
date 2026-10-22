"""Test setup for the Laya detector tests: a fake detector and a slow-test gate.

This lives in its own folder, not tests/conftest.py, so it stays out of the way
of other generalize test setup. Tests marked ``slow`` run the real model and are
skipped unless METASKILL_RUN_SLOW=1 is set.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SLOW_ENV = "METASKILL_RUN_SLOW"

sys.path.insert(0, os.path.join(HERE, "..", ".."))

from metaskill.generalize import laya_pii  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"slow: runs the real Laya model (~1GB download); set {SLOW_ENV}=1 to include",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get(SLOW_ENV):
        return
    skip_slow = pytest.mark.skip(reason=f"needs {SLOW_ENV}=1 (downloads the Laya model)")
    for item in items:
        if "slow" in item.keywords and str(item.path).startswith(HERE):
            item.add_marker(skip_slow)


class FakeDetector:
    """Stands in for laya's PiiRedactor: finds one email and one phone number."""

    NEEDLES = (("priya.nair@example.com", "email address"), ("+44 7700 900123", "phone number"))

    def detect(self, text, threshold=0.5, region="US"):
        entities = []
        for needle, label in self.NEEDLES:
            start = text.find(needle)
            if start >= 0:
                entities.append(
                    SimpleNamespace(
                        start=start, end=start + len(needle), text=needle, label=label, score=0.9876543
                    )
                )
        return SimpleNamespace(entities=entities)


@pytest.fixture(autouse=True)
def _fresh_model_cache(monkeypatch):
    """Each test starts with an empty model cache so tests cannot leak a loaded model."""
    monkeypatch.setattr(laya_pii, "_MODELS", {})


@pytest.fixture
def fake_detector_class():
    return FakeDetector


@pytest.fixture
def fake_laya(monkeypatch):
    """Replace model loading with FakeDetector: no laya install, no download."""
    monkeypatch.setattr(laya_pii, "load_detector", lambda model_path="": FakeDetector())
