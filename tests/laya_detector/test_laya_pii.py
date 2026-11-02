"""Tests for the Laya PII detector wrapper (metaskill/generalize/laya_pii/).

Fast tests use the fake detector from conftest.py (no model, no network).
Run the real model test with:  METASKILL_RUN_SLOW=1 pytest -q
"""

from __future__ import annotations

import sys
import types

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


PII_DEMO = "metaskill.generalize.laya_pii.pii_demo"


def test_missing_laya_raises_with_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, PII_DEMO, None)

    with pytest.raises(laya_pii.LayaUnavailable, match="metaskill-laya"):
        laya_pii.scan(_TEXT)


def test_missing_laya_package_alone_raises_with_install_hint(monkeypatch):
    # numpy/torch present but `import laya` fails: must still be the friendly error.
    pytest.importorskip("numpy")
    pytest.importorskip("torch")
    monkeypatch.setitem(sys.modules, "laya", None)

    with pytest.raises(laya_pii.LayaUnavailable, match="metaskill-laya"):
        laya_pii.load_detector()


def _fake_pii_demo(monkeypatch):
    """Install a stand-in pii_demo module that records how the detector is built."""
    built = []

    class Recorder:
        def __init__(self, model, device=None):
            built.append({"model": model, "device": device})

    module = types.ModuleType(PII_DEMO)
    module.PiiRedactor = Recorder
    monkeypatch.setitem(sys.modules, PII_DEMO, module)
    return built


def test_detector_runs_on_cpu_by_default(monkeypatch):
    monkeypatch.delenv(laya_pii.DEVICE_ENV, raising=False)
    built = _fake_pii_demo(monkeypatch)

    laya_pii.load_detector()

    assert built == [{"model": laya_pii.HUB_REPO, "device": "cpu"}]


def test_detector_device_can_be_overridden(monkeypatch):
    monkeypatch.setenv(laya_pii.DEVICE_ENV, "cuda")
    built = _fake_pii_demo(monkeypatch)

    laya_pii.load_detector("/models/pii")

    assert built == [{"model": "/models/pii", "device": "cuda"}]


def test_detector_is_cached_per_model_path():
    cached = object()
    laya_pii._MODELS["/models/pii"] = cached

    assert laya_pii.load_detector("/models/pii") is cached


@pytest.mark.slow
def test_real_laya_detection_runs_locally():
    entities = laya_pii.scan(_TEXT)

    assert {"email address", "phone number"} <= {e["label"] for e in entities}
    assert "" in laya_pii._MODELS  # the real model was loaded, not a stand-in


def test_skip_nested_pieces_drops_fragments_of_a_larger_candidate():
    # pii_demo imports numpy and torch at module level
    pytest.importorskip("numpy")
    pytest.importorskip("torch")
    from metaskill.generalize.laya_pii.pii_demo import build_states, skip_nested_pieces

    text = "Mail priya1990@acme.com or call 416-978-2011 on 4 March."
    rows = build_states(text)
    kept = {text[s:e] for (s, e), _, _ in skip_nested_pieces(rows)}
    dropped = {text[s:e] for (s, e), _, _ in rows} - kept

    assert {"priya1990@acme.com", "416-978-2011", "4 March"} <= kept
    assert {"priya1990", "416", "978", "2011"} <= dropped  # pieces of the email and the phone
    assert "March" in kept or "4 March" in kept  # a date is not a container, so it survives


def test_resolve_device_defaults_to_cpu_and_passes_explicit_devices_through():
    assert laya_pii.resolve_device("") == "cpu"
    assert laya_pii.resolve_device("CPU") == "cpu"
    assert laya_pii.resolve_device("cuda:1") == "cuda:1"
    assert laya_pii.resolve_device("mps") == "mps"


def test_resolve_device_gpu_mode_uses_cuda_only_when_torch_sees_one(monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert laya_pii.resolve_device("gpu") == "cuda"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert laya_pii.resolve_device("gpu") == "cpu"


def test_resolve_device_gpu_mode_without_torch_falls_back_to_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    assert laya_pii.resolve_device("gpu") == "cpu"
