"""The plugin as Hermes loads it: under a name of its own, with `metaskill` not importable.

An absolute `metaskill.` import works in every other test, because pytest runs from the
repo root, so it only fails here. The check runs in its own process, from outside the repo.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Run with `python -I`: no current folder and no PYTHONPATH on the path.
SCRIPT = """
import importlib, importlib.util, json, sys
from pathlib import Path

root, skill, reports = sys.argv[1:]
assert importlib.util.find_spec("metaskill") is None, "metaskill is importable: nothing is tested"
name = "hermes_plugin_under_test"
spec = importlib.util.spec_from_file_location(
    name, Path(root) / "__init__.py", submodule_search_locations=[root])
plugin = importlib.util.module_from_spec(spec)
sys.modules[name] = plugin
spec.loader.exec_module(plugin)

class Ctx:
    tools = []
    def register_tool(self, **kwargs): self.tools.append(kwargs)
    def register_command(self, *args, **kwargs): pass

ctx = Ctx()
plugin.register(ctx)

# Some modules are only imported when they are needed, so import every one.
package = Path(root) / "metaskill"
for path in sorted(package.rglob("*.py")):
    parts = path.relative_to(package).with_suffix("").parts
    parts = parts[:-1] if parts[-1] == "__init__" else parts
    importlib.import_module(".".join((name, "metaskill", *parts)))

reply = ctx.tools[0]["handler"]({"operation": "rank", "skill_path": skill, "output_path": reports})
print(json.dumps({"reply": json.loads(reply), "top_level": sorted(
    m for m in sys.modules if m == "metaskill" or m.startswith("metaskill."))}))
"""


def test_plugin_runs_under_another_name_from_outside_the_repo(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n\nSummarize the file.\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT, str(ROOT), str(skill), str(tmp_path / "reports")],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.splitlines()[-1])
    assert result["reply"]["status"] == "ok", result["reply"]
    assert result["reply"]["counts"]["skills"] == 1
    assert result["top_level"] == []
