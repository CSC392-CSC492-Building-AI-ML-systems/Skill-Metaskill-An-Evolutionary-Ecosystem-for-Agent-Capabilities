"""Tests for the Laya PII detector wrapper (metaskill/generalize/laya_pii.py).

Fast tests use the fake detector from conftest.py (no model, no network).
Run the real model test with:  pytest -q --runslow
"""

from __future__ import annotations

import sys

import pytest

from metaskill.generalize import laya_pii

_TEXT = (
    "Hi, I am Priya Nair. Call me on +44 7700 900123 or email "
    "priya.nair@example.com."
)


def test_scan_returns_entities(fake_laya):
    entities = laya_pii.scan(_TEXT)

    assert {e["label"] for e in entities} == {"email address", "phone number"}
    assert {e["category"] for e in entities} == {"email_address", "phone_number"}
    for e in entities:
        assert set(e) == {"start", "end", "text", "label", "score", "category"}
        assert _TEXT[e["start"]:e["end"]] == e["text"]
        assert e["score"] == 0.9877


def test_scan_skips_empty_text():
    # No fixture: an empty scan must not try to load a model at all.
    assert laya_pii.scan("") == []
    assert laya_pii.scan("   ") == []


def test_missing_laya_raises_with_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "metaskill.generalize.pii_demo", None)

    with pytest.raises(laya_pii.LayaUnavailable, match="metaskill-laya"):
        laya_pii.scan(_TEXT)


def test_missing_laya_package_alone_raises_with_install_hint(monkeypatch):
    # numpy/torch present but `import laya` fails: must still be the friendly error.
    pytest.importorskip("numpy")
    pytest.importorskip("torch")
    monkeypatch.setitem(sys.modules, "laya", None)

    with pytest.raises(laya_pii.LayaUnavailable, match="metaskill-laya"):
        laya_pii.load_detector()


def test_detector_is_cached_per_model_path():
    cached = object()
    laya_pii._MODELS["/models/pii"] = cached

    assert laya_pii.load_detector("/models/pii") is cached


@pytest.mark.slow
def test_real_laya_detection_runs_locally():
    entities = laya_pii.scan(_TEXT)

    assert {"email address", "phone number"} <= {e["label"] for e in entities}
    assert "" in laya_pii._MODELS  # the real model was loaded, not a stand-in
