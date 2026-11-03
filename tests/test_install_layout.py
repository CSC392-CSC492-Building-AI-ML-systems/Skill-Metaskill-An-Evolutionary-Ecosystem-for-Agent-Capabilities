"""The plugins must work when Hermes loads them as packages under their own names.

Hermes does not put the repo root on sys.path, so a top-level ``import metaskill``
inside the plugin would fail once installed, and the metaskill-laya add-on is a
separate package that cannot import metaskill at all. The other tests add the repo
root to sys.path and cannot see either problem, so this one runs a fresh interpreter
(``-I``) from another directory and loads each plugin from its folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_SCRIPT = textwrap.dedent(
    """
    import importlib.util, json, pathlib, sys, tempfile, types

    repo = pathlib.Path(sys.argv[1])
    with_addon = sys.argv[2] == "1"
    sys.modules["hermes_plugins"] = types.ModuleType("hermes_plugins")
    sys.modules["hermes_plugins"].__path__ = []

    def load(name, folder):
        spec = importlib.util.spec_from_file_location(
            f"hermes_plugins.{name}", folder / "__init__.py", submodule_search_locations=[str(folder)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    class Ctx:
        def __getattr__(self, name):  # accept every registration
            return lambda *args, **kwargs: None

    core = load("metaskill", repo)
    core.register(Ctx())
    if with_addon:
        load("metaskill_laya", repo / "plugins" / "metaskill-laya").register(Ctx())

    skill = pathlib.Path(tempfile.mkdtemp()) / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Contact priya.nair@acme-corp.com for access.")
    args = {"operation": "generalize", "skill_path": str(skill)}
    print(json.dumps(json.loads(core.tools.metaskill(args))))
    """
)


def _generalize(tmp_path, with_addon: bool) -> dict:
    env = dict(os.environ, METASKILL_EXTRAS_MAX_SECONDS="600")
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _SCRIPT, REPO, "1" if with_addon else "0"],
        cwd=tmp_path, capture_output=True, text=True, timeout=600, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_generalize_loads_when_installed_as_a_package(tmp_path):
    result = _generalize(tmp_path, with_addon=False)
    # "not implemented yet" would mean the generalize entry point failed to import.
    assert result["status"] == "ok", result
    assert result["detectors"] == ["regex"]
    assert result["counts"]["by_category"] == {"email": 1}
    assert "metaskill-laya" in result["note"]


def test_addon_reaches_core_when_both_are_installed(tmp_path):
    result = _generalize(tmp_path, with_addon=True)
    assert result["status"] == "ok", result
    assert "note" not in result  # core saw the add-on's registration
    if "laya" not in result["detectors"]:  # laya itself is not installed here
        [warning] = result["warnings"]
        assert warning.startswith("laya detector failed (LayaError): the laya package is not importable")
