"""Tests for the metaskill-laya add-on (plugins/metaskill-laya).

Fast tests stub the model and need neither laya nor a download. Run the real-model
tests with:  METASKILL_RUN_SLOW=1 pytest -q tests/laya_addon
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from types import SimpleNamespace

import pytest

from conftest import ADDON_DIR, FakeCtx, load_addon
from metaskill.generalize import extras, pipeline

TEXT = ("Contact Priya Nair at priya.nair@example.com or +44 7700 900123.\n"
        "Her office is at 12 Baker Street, London, and she was born on 4 March 1990.\n")


class FakeRedactor:
    """Stands in for pii_demo.PiiRedactor: reports "Priya Nair" unless it is skipped."""

    def __init__(self):
        self.calls = []

    def detect(self, text, threshold=0.5, region="US", skip=(), deadline=None):
        self.calls.append((threshold, list(skip)))
        self.deadline = deadline
        start = text.find("Priya Nair")
        hit = SimpleNamespace(start=start, end=start + 10, label="given name", score=0.98765)
        overlaps = any(s < hit.end and hit.start < e for s, e in skip)
        return SimpleNamespace(entities=[] if start < 0 or overlaps else [hit])


# --- registration ---------------------------------------------------------------

def test_register_adds_the_detector_and_unload_removes_it(addon, ctx):
    addon.register(ctx)
    [entry] = extras.registered()  # core sees it through the shared registry
    assert entry is addon.ENTRY
    assert (entry["api"], entry["name"]) == (extras.API, "laya")
    assert [name for name, _, _ in ctx.cli] == ["metaskill-laya"]

    [unload] = ctx.unload
    unload()
    assert extras.registered() == []


def test_unload_leaves_a_newer_registration_alone(addon, ctx):
    addon.register(ctx)
    newer = dict(addon.ENTRY)
    addon._registry()["laya"] = newer  # e.g. the plugin was reloaded
    ctx.unload[0]()
    assert addon._registry()["laya"] is newer


def test_register_works_on_an_older_hermes(addon):
    addon.register(object())  # no on_unload, register_cli_command or register_skill
    assert [e["name"] for e in extras.registered()] == ["laya"]


def test_register_bundles_the_setup_skill(addon, ctx):
    addon.register(ctx)
    [(name, path, description, frontmatter)] = ctx.skills
    assert name == "setup" and "GPU" in description
    # Hermes expects the SKILL.md file itself and raises if it is missing.
    assert path.name == "SKILL.md" and path.is_file()
    assert frontmatter == {"metadata": {"hermes": {"requires_tools": ["metaskill"]}}}


def test_register_skill_falls_back_to_name_and_path(addon):
    class OldCtx:
        def __init__(self):
            self.skills = []

        def register_skill(self, name, path):
            self.skills.append(name)
    ctx = OldCtx()
    addon.register(ctx)
    assert ctx.skills == ["setup"]


def test_setup_skill_frontmatter_matches_the_registration(addon):
    text = addon.SETUP_SKILL.read_text(encoding="utf-8")
    header = text.split("---")[1]
    assert header.startswith("\nname: setup\n")
    assert "requires_tools: [metaskill]" in header


def test_setup_skill_covers_setup_gpu_and_warnings(addon):
    text = addon.SETUP_SKILL.read_text(encoding="utf-8")
    detector = addon.detector
    for needle in (
        "hermes metaskill-laya status", "hermes metaskill-laya download",
        f"hermes config set {detector.DEVICE_ENV} gpu", detector.MODEL_ENV,
        "METASKILL_EXTRAS_MAX_SECONDS", "hermes pm repair", "Ask the user",
        "not importable", "could not load the Laya model", "stopped at the time limit",
    ):
        assert needle in text, needle


def test_setup_skill_quotes_the_real_warnings(addon):
    # The skill tells the agent what each generalize warning means; keep it in step.
    text = addon.SETUP_SKILL.read_text(encoding="utf-8")
    assert addon.detector.install_hint("<name>").startswith("the <name> package is not importable")
    assert "`the <name> package is not importable`" in text
    assert "`laya stopped at the time limit after N of M files`" in text


def test_registry_name_matches_core(addon):
    assert addon.REGISTRY == extras.REGISTRY
    assert addon.ENTRY["api"] == extras.API


def test_register_does_not_import_laya_or_torch(monkeypatch, addon, ctx):
    monkeypatch.setitem(sys.modules, "laya", None)  # importing either would now fail
    monkeypatch.setitem(sys.modules, "torch", None)
    addon.register(ctx)


# --- detection ------------------------------------------------------------------

def test_detect_returns_plain_hits_and_passes_skip(monkeypatch, addon):
    fake = FakeRedactor()
    monkeypatch.setattr(addon.detector, "load", lambda: fake)
    hits = addon.detector.detect(TEXT, [(22, 44)])
    assert hits == [{"start": 8, "end": 18, "label": "given name", "score": 0.9877}]
    assert fake.calls == [(addon.detector.THRESHOLD, [(22, 44)])]


def test_detect_passes_the_deadline_to_the_model(monkeypatch, addon):
    fake = FakeRedactor()
    monkeypatch.setattr(addon.detector, "load", lambda: fake)
    addon.detector.detect(TEXT, deadline=123.0)
    assert fake.deadline == 123.0


def test_scoring_stops_between_batches_at_the_deadline(monkeypatch, addon):
    pytest.importorskip("numpy")  # pii_demo imports numpy and torch at module level
    pytest.importorskip("torch")
    from hermes_plugins.metaskill_laya import pii_demo

    common = SimpleNamespace(QTYPES={"choice": 0}, temp_bucket=lambda *a: 0,
                             build_sequence=None, collate_items=None)  # never reached
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(common=common))
    monkeypatch.setitem(sys.modules, "laya.common", common)
    redactor = pii_demo.PiiRedactor.__new__(pii_demo.PiiRedactor)
    redactor.batch_size, redactor.question = 16, {}
    redactor.agent = SimpleNamespace(cfg={}, tok=None, temperature_by_options={}, temperature=[1.0],
                                     _to_internal=lambda q: q)
    with pytest.raises(TimeoutError):
        redactor.score([{}], deadline=0.0)  # time.monotonic() is already past 0


@pytest.mark.parametrize("value, label, expected", [
    ("4111 1111 1111 1111", "credit card number", True),
    ("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "credit card number", False),
    ("555-0100", "phone number", False),
    ("(702) 555-0142", "phone number", False),
    ("+1 702 555 0142", "phone number", False),
    ("555-0100", "social security number", False),  # labels are unreliable; the value decides
    ("+44 7700 900123", "phone number", True),
    ("416-978-2011", "phone number", True),
    ("078-05-1120", "credit card number", True),  # mislabeled, but a real number: kept
    ("5550100", "given name", True),  # only number labels are checked
    ("1", "postal code", False),
    ("78701", "postal code", True),
    ("M5V 2T6", "postal code", True),
    ("12", "building number", True),
])
def test_plausible_drops_only_impossible_values(addon, value, label, expected):
    assert addon.detector.plausible(value, label) is expected


def test_detect_drops_implausible_hits(monkeypatch, addon):
    text = "Card 9f86d081884c and phone 555-0100 and Priya Nair"

    class Model:
        def detect(self, text, threshold=0.5, region="US", skip=(), deadline=None):
            spans = [("9f86d081884c", "credit card number"), ("555-0100", "phone number"),
                     ("Priya Nair", "given name")]
            return SimpleNamespace(entities=[
                SimpleNamespace(start=text.find(v), end=text.find(v) + len(v), label=label, score=0.9)
                for v, label in spans])
    monkeypatch.setattr(addon.detector, "load", Model)
    assert [h["label"] for h in addon.detector.detect(text)] == ["given name"]


def test_detect_skips_blank_text_without_loading(addon):
    assert addon.detector.detect("") == []
    assert addon.detector.detect("  \n") == []
    assert addon.detector._MODELS == {}


def test_generalize_runs_the_addon_after_regex(monkeypatch, addon, ctx, tmp_path):
    fake = FakeRedactor()
    monkeypatch.setattr(addon.detector, "load", lambda: fake)
    addon.register(ctx)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(TEXT, encoding="utf-8", newline="")

    result = pipeline.generalize_skill(str(skill), "out")
    assert result["detectors"] == ["regex", "laya"]
    assert result["counts"]["by_detector"]["laya"] == 1
    [(_, skip)] = fake.calls
    email = TEXT.find("priya.nair@")
    assert (email, email + len("priya.nair@example.com")) in skip
    assert "Priya" not in json.dumps(result)


@pytest.mark.parametrize("name, package", [
    ("laya", "laya"), ("torch", "torch"), ("torch.nn", "torch"), ("numpy", "numpy"), (None, "laya"),
])
def test_a_missing_package_is_named_in_the_hint(monkeypatch, addon, name, package):
    class Missing:
        def __init__(self, **kwargs):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    pii_demo = SimpleNamespace(PiiRedactor=Missing)
    monkeypatch.setitem(sys.modules, "hermes_plugins.metaskill_laya.pii_demo", pii_demo)
    monkeypatch.setattr(sys.modules["hermes_plugins.metaskill_laya"], "pii_demo", pii_demo, raising=False)
    with pytest.raises(addon.detector.LayaError) as raised:
        addon.detector.load()
    assert raised.value.hint.startswith(f"the {package} package is not importable")
    assert "restart Hermes" in raised.value.hint


def test_model_load_failure_raises_a_hint(monkeypatch, addon):
    class Broken:
        def __init__(self, **kwargs):
            raise ValueError("digest mismatch for model.safetensors")
    pii_demo = SimpleNamespace(PiiRedactor=Broken)
    monkeypatch.setitem(sys.modules, "hermes_plugins.metaskill_laya.pii_demo", pii_demo)
    monkeypatch.setattr(sys.modules["hermes_plugins.metaskill_laya"], "pii_demo", pii_demo, raising=False)
    with pytest.raises(addon.detector.LayaError, match="ValueError: digest mismatch"):
        addon.detector.load()


def test_model_is_loaded_once_per_setting(monkeypatch, addon):
    built = []

    class Recording:
        def __init__(self, **kwargs):
            built.append(kwargs)
    pii_demo = SimpleNamespace(PiiRedactor=Recording)
    monkeypatch.setitem(sys.modules, "hermes_plugins.metaskill_laya.pii_demo", pii_demo)
    monkeypatch.setattr(sys.modules["hermes_plugins.metaskill_laya"], "pii_demo", pii_demo, raising=False)

    first = addon.detector.load()
    assert addon.detector.load() is first
    monkeypatch.setenv(addon.detector.DEVICE_ENV, "cuda:1")
    assert addon.detector.load() is not first
    assert [b["device"] for b in built] == ["cpu", "cuda:1"]
    assert built[0]["revision"] == addon.detector.MODEL_REVISION
    assert built[0]["expected_sha256"] == addon.detector.MODEL_SHA256


# --- settings -------------------------------------------------------------------

def test_hub_model_is_pinned_and_verified(addon):
    source = addon.detector.model_source("")
    assert source["model"] == "goku-san/laya-experts" and source["subfolder"] == "pii"
    assert re.fullmatch(r"[0-9a-f]{40}", source["revision"])
    assert re.fullmatch(r"[0-9a-f]{64}", source["expected_sha256"]["model.safetensors"])


def test_local_model_checkout_or_pii_folder(addon, tmp_path):
    (tmp_path / "pii").mkdir()
    assert addon.detector.model_source(str(tmp_path)) == {"model": str(tmp_path), "subfolder": "pii"}
    pii = tmp_path / "pii"
    assert addon.detector.model_source(str(pii)) == {"model": str(pii), "subfolder": None}
    with pytest.raises(addon.detector.LayaError, match="not a directory"):
        addon.detector.model_source(str(tmp_path / "missing"))


def test_resolve_device_defaults_to_cpu_and_passes_explicit_devices(addon):
    resolve = addon.detector.resolve_device
    assert [resolve(""), resolve("CPU"), resolve("cuda:1")] == ["cpu", "cpu", "cuda:1"]


@pytest.mark.parametrize("setting", ["mps", "MPS", " mps ", "mps:0", "auto", "AUTO"])
def test_resolve_device_refuses_devices_that_can_hang(addon, setting):
    with pytest.raises(addon.detector.LayaError, match="is not supported") as raised:
        addon.detector.resolve_device(setting)
    assert "cpu or gpu" in raised.value.hint


def test_refused_device_reaches_generalize_as_a_warning(monkeypatch, addon, ctx, tmp_path):
    monkeypatch.setenv(addon.detector.DEVICE_ENV, "mps:0")
    addon.register(ctx)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(TEXT, encoding="utf-8", newline="")
    result = pipeline.generalize_skill(str(skill), "out")
    assert result["detectors"] == ["regex"]
    assert any("METASKILL_LAYA_DEVICE=mps:0 is not supported" in w for w in result["warnings"])
    assert addon.detector._MODELS == {}


def test_resolve_device_gpu_uses_cuda_only_when_torch_sees_one(monkeypatch, addon):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert addon.detector.resolve_device("gpu") == "cuda"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert addon.detector.resolve_device("gpu") == "cpu"


def test_resolve_device_gpu_without_torch_falls_back_to_cpu(monkeypatch, addon):
    monkeypatch.setitem(sys.modules, "torch", None)
    assert addon.detector.resolve_device("gpu") == "cpu"


# --- vendored model code --------------------------------------------------------

def test_skip_helpers_drop_fragments_and_claimed_spans():
    pytest.importorskip("numpy")  # pii_demo imports numpy and torch at module level
    pytest.importorskip("torch")
    from hermes_plugins.metaskill_laya.pii_demo import build_states, skip_claimed, skip_nested_pieces

    text = "Mail priya1990@acme.com or call 416-978-2011 on 4 March."
    rows = build_states(text)
    kept = {text[s:e] for (s, e), _, _ in skip_nested_pieces(rows)}
    assert {"priya1990@acme.com", "416-978-2011", "4 March"} <= kept
    assert not {"priya1990", "416", "978", "2011"} & kept  # pieces of the email and the phone

    email = (text.find("priya1990@"), text.find(" or call"))
    after = [span for span, _, _ in skip_claimed(rows, [email])]
    assert not any(s < email[1] and email[0] < e for s, e in after)
    assert {"4 March", "416-978-2011"} <= {text[s:e] for s, e in after}


# --- packaging ------------------------------------------------------------------

def _plugin_yaml_dependency():
    text = (ADDON_DIR / "plugin.yaml").read_text(encoding="utf-8")
    return re.search(r'python_dependencies:\s*\n\s*-\s*"([^"]+)"', text).group(1)


def test_dev_requirements_match_the_manifest():
    lines = (ADDON_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line and not line.startswith("#")] == [_plugin_yaml_dependency()]


def test_laya_range_is_bounded():
    assert re.fullmatch(r"laya>=[\d.]+,<[\d.]+", _plugin_yaml_dependency())


def test_vendored_code_carries_its_license_and_revision(addon):
    header = (ADDON_DIR / "pii_demo.py").read_text(encoding="utf-8")[:600]
    assert addon.detector.MODEL_REVISION in header and "Apache License 2.0" in header
    assert "Apache License" in (ADDON_DIR / "LICENSE").read_text(encoding="utf-8")[:200]
    assert addon.detector.MODEL_REVISION in (ADDON_DIR / "NOTICE").read_text(encoding="utf-8")


# --- CLI ------------------------------------------------------------------------

def _cli_args(addon, *argv):
    parser = argparse.ArgumentParser()
    addon.cli.setup(parser)
    return parser.parse_args(list(argv))


def test_cli_status_reports_a_missing_laya(monkeypatch, addon, capsys):
    monkeypatch.setattr(addon.cli, "_version", lambda package: "not installed")
    assert addon.cli.handle(_cli_args(addon, "status")) == 1
    assert "laya:   not installed" in capsys.readouterr().out


def test_cli_status_explains_a_gpu_setting_without_a_gpu(monkeypatch, addon, capsys):
    monkeypatch.setenv(addon.detector.DEVICE_ENV, "gpu")
    monkeypatch.setattr(addon.detector, "resolve_device", lambda setting: "cpu")
    addon.cli.handle(_cli_args(addon, "status"))
    out = capsys.readouterr().out
    assert "device: cpu (METASKILL_LAYA_DEVICE=gpu, but PyTorch sees no CUDA or ROCm GPU" in out


def test_cli_status_reports_a_refused_device(monkeypatch, addon, capsys):
    monkeypatch.setenv(addon.detector.DEVICE_ENV, "mps")
    assert addon.cli.handle(_cli_args(addon, "status")) == 1
    assert "device: refused (METASKILL_LAYA_DEVICE=mps is not supported" in capsys.readouterr().out


def test_cli_status_shows_how_to_turn_on_the_gpu(addon, capsys):
    addon.cli.handle(_cli_args(addon, "status"))
    assert "device: cpu (to use a GPU: hermes config set METASKILL_LAYA_DEVICE gpu)" in (
        capsys.readouterr().out)


def test_cli_download_loads_and_self_checks(monkeypatch, addon, capsys):
    monkeypatch.setattr(addon.detector, "load", lambda: FakeRedactor())
    assert addon.cli.handle(_cli_args(addon, "download")) == 0
    assert "Self-check found 1 items" in capsys.readouterr().out


def test_cli_download_reports_the_hint(monkeypatch, addon, capsys):
    def fail():
        raise addon.detector.LayaError("restart Hermes")
    monkeypatch.setattr(addon.detector, "load", fail)
    assert addon.cli.handle(_cli_args(addon, "download")) == 1
    assert "error: restart Hermes" in capsys.readouterr().out


# --- real model (slow) ----------------------------------------------------------

@pytest.mark.slow
def test_real_model_finds_free_form_personal_data(addon):
    pytest.importorskip("laya")
    labels = {h["label"] for h in addon.detector.detect(TEXT)}
    assert {"given name", "surname", "street", "date"} <= labels
    [model] = addon.detector._MODELS.values()
    assert model.agent.revision == addon.detector.MODEL_REVISION


@pytest.mark.slow
def test_real_model_stops_at_the_deadline(addon):
    pytest.importorskip("laya")
    addon.detector.load()  # the limit does not cover loading
    with pytest.raises(TimeoutError):
        addon.detector.detect(TEXT, deadline=0.0)


@pytest.mark.slow
def test_real_model_skips_claimed_spans(addon):
    pytest.importorskip("laya")
    email = TEXT.find("priya.nair@")
    skip = [(email, email + len("priya.nair@example.com"))]
    hits = addon.detector.detect(TEXT, skip)
    assert hits and not any(h["start"] < skip[0][1] and skip[0][0] < h["end"] for h in hits)


@pytest.mark.slow
def test_real_model_from_a_local_copy(monkeypatch, addon):
    pytest.importorskip("laya")
    from huggingface_hub import snapshot_download

    local = snapshot_download(addon.detector.HUB_REPO, revision=addon.detector.MODEL_REVISION,
                              allow_patterns=["pii/*"])
    monkeypatch.setenv(addon.detector.MODEL_ENV, local)
    assert "date" in {h["label"] for h in addon.detector.detect(TEXT)}
    [key] = addon.detector._MODELS
    assert key[0] == local
