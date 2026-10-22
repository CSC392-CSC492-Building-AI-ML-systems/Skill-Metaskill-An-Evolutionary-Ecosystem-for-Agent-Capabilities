"""Tests for generalize's Laya PII detection, through the metaskill tool.

Fast tests use the fake detector from conftest.py (no model, no network).
Run the real model test with:  METASKILL_RUN_SLOW=1 pytest -q
"""

from __future__ import annotations

import json
import sys

import pytest

import tools
from metaskill.generalize import laya_pii

SAMPLE_SKILL = """\
# Laya Sample Skill

Contact priya.nair@example.com for access.
"""


@pytest.fixture
def sample_skill(tmp_path):
    skill_dir = tmp_path / "laya-sample"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    return skill_dir


def _generalize(skill, **extra):
    args = {"operation": "generalize", "skill_path": str(skill), "use_laya": True, **extra}
    return json.loads(tools.metaskill(args))


def test_laya_is_not_used_unless_opted_in(monkeypatch, sample_skill):
    def boom(model_path=""):
        raise AssertionError("laya must not load without use_laya")

    monkeypatch.setattr(laya_pii, "load_detector", boom)

    data = _generalize(sample_skill, use_laya=False)

    assert data["status"] == "error"
    assert "not implemented" in data["error"]


def test_generalize_reports_counts(fake_laya, sample_skill):
    data = _generalize(sample_skill)

    assert data["status"] == "ok"
    assert data["counts"] == {"entities_detected": 1, "files_scanned": 1}


def test_generalize_never_returns_detected_values(fake_laya, sample_skill):
    raw = tools.metaskill(
        {"operation": "generalize", "skill_path": str(sample_skill), "use_laya": True}
    )

    assert "priya.nair" not in raw


def test_unsupported_files_are_not_scanned(fake_laya, sample_skill):
    (sample_skill / "data.bin").write_bytes(b"priya.nair@example.com")

    assert _generalize(sample_skill)["counts"]["files_scanned"] == 1


def test_result_reports_that_laya_was_used(fake_laya, sample_skill):
    assert _generalize(sample_skill)["detectors"] == ["laya"]


def test_model_path_is_passed_to_the_detector(monkeypatch, sample_skill, fake_detector_class):
    seen = []

    def fake_load(model_path=""):
        seen.append(model_path)
        return fake_detector_class()

    monkeypatch.setattr(laya_pii, "load_detector", fake_load)

    _generalize(sample_skill, laya_model_path="/models/pii")

    assert seen and set(seen) == {"/models/pii"}


def test_missing_laya_is_an_error_not_a_silent_fallback(monkeypatch, sample_skill):
    monkeypatch.setitem(sys.modules, "metaskill.generalize.laya_pii.pii_demo", None)

    data = _generalize(sample_skill)

    assert data["status"] == "error"
    assert data["success"] is False
    assert "metaskill-laya" in data["error"]
    assert data["laya_available"] is False
    # The model is told to ask the user first, not to install on its own.
    assert "Ask the user" in data["next_step"]
    assert "hermes plugins install metaskill-laya" in data["next_step"]


@pytest.mark.slow
def test_real_laya_finds_the_email(sample_skill):
    data = _generalize(sample_skill)

    assert data["status"] == "ok"
    assert data["counts"]["entities_detected"] >= 1
    assert "" in laya_pii._MODELS  # the real model ran
