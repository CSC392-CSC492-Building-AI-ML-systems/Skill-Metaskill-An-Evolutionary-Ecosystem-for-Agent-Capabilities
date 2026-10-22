"""The plugin must work when Hermes loads it as a package under its own name.

Hermes does not put the repo root on sys.path, so a top-level
``import metaskill`` inside the plugin would fail once installed. The other
tests add the repo root to sys.path and cannot see that, so this one runs a
fresh interpreter (``-I``) from another directory.
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
    import importlib.util, json, sys, tempfile, pathlib

    repo = sys.argv[1]
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins_metaskill", f"{repo}/__init__.py", submodule_search_locations=[repo]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_plugins_metaskill"] = pkg
    spec.loader.exec_module(pkg)

    skill = pathlib.Path(tempfile.mkdtemp()) / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("hello")
    args = {"operation": "generalize", "skill_path": str(skill), "use_laya": True}
    print(json.dumps(json.loads(pkg.tools.metaskill(args))))
    """
)


def test_generalize_loads_when_installed_as_a_package(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _SCRIPT, REPO],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    # "not implemented yet" means the generalize entry point failed to import.
    assert "not implemented" not in result.get("error", "")
    if result["status"] == "error":
        assert "metaskill-laya" in result["error"]
