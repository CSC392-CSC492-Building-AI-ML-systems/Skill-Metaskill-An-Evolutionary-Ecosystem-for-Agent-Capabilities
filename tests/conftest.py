"""Shared test setup: --runslow for model tests, and a fake Laya detector."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from metaskill.generalize import laya_pii  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="run tests marked slow (they download and run the real Laya model)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --runslow (downloads the Laya model)")
    for item in items:
        if "slow" in item.keywords:
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
def fake_laya(monkeypatch):
    """Replace model loading with FakeDetector: no laya install, no download."""
    monkeypatch.setattr(laya_pii, "load_detector", lambda model_path="": FakeDetector())
