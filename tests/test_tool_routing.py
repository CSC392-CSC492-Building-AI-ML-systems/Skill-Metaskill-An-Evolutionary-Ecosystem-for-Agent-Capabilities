import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import schemas  # noqa: E402
import tools  # noqa: E402


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """A cwd containing the paths the tests refer to, since run() checks they exist."""
    for directory in ("skills/gcp", "out/t", "x"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "c.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_pipelines(monkeypatch):
    """Install stand-in pipelines so routing can be tested before they exist."""
    calls = {}

    def generalize_skill(skill_path, output_path):
        calls["generalize"] = (skill_path, output_path)
        return {"artifacts": [output_path], "counts": {"findings": 2}}

    def personalize_skill(skill_path, config_path, output_path):
        calls["personalize"] = (skill_path, config_path, output_path)
        return {"artifacts": [output_path], "counts": {"substitutions": 3}}

    for name, func in (("generalize", generalize_skill), ("personalize", personalize_skill)):
        module = types.ModuleType(f"metaskill.{name}")
        setattr(module, f"{name}_skill", func)
        monkeypatch.setitem(sys.modules, f"metaskill.{name}", module)
    return calls


def test_schema_requires_operation_and_skill_path():
    params = schemas.METASKILL["parameters"]
    assert params["required"] == ["operation", "skill_path"]
    assert params["properties"]["operation"]["enum"] == [
        "generalize", "personalize"]


def test_schema_stays_small():
    # Sent to the model every turn; guard against it growing unnoticed.
    assert len(json.dumps(schemas.METASKILL)) < 1500


def test_generalize_routes_with_default_output(fake_pipelines):
    result = json.loads(tools.metaskill(
        {"operation": "generalize", "skill_path": "skills/gcp"}))
    expected_output = os.path.join("metaskill-output", "gcp-template")
    assert fake_pipelines["generalize"] == ("skills/gcp", expected_output)
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["counts"] == {"findings": 2}
    assert result["unresolved"] == [] and result["warnings"] == []


def test_default_output_uses_directory_name_for_skill_md():
    path = tools.default_output_path(
        "generalize", os.path.join("skills", "gcp", "SKILL.md"))
    assert path == os.path.join("metaskill-output", "gcp-template")


def test_personalize_routes_config(fake_pipelines):
    args = {"operation": "personalize", "skill_path": "out/t",
            "config_path": "c.json", "output_path": "o"}
    result = json.loads(tools.metaskill(args))
    assert fake_pipelines["personalize"] == ("out/t", "c.json", "o")
    assert result["operation"] == "personalize"


def test_personalize_requires_config(fake_pipelines):
    result = json.loads(tools.metaskill(
        {"operation": "personalize", "skill_path": "out/t"}))
    assert result["success"] is False
    assert "config_path" in result["error"]
    assert "personalize" not in fake_pipelines


def test_unknown_operation_rejected():
    result = json.loads(tools.metaskill(
        {"operation": "evaluate", "skill_path": "x"}))
    assert result["success"] is False


def test_skill_name_instead_of_path_tells_model_to_resolve(fake_pipelines):
    result = json.loads(tools.metaskill(
        {"operation": "generalize", "skill_path": "gcp-file-transfer"}))
    assert result["success"] is False
    assert "find the skill's directory" in result["error"]
    assert "generalize" not in fake_pipelines


def test_missing_config_file_rejected(fake_pipelines):
    args = {"operation": "personalize",
            "skill_path": "out/t", "config_path": "nope.json"}
    result = json.loads(tools.metaskill(args))
    assert result["success"] is False
    assert "config_path" in result["error"]


def test_schema_tells_model_to_resolve_names():
    description = schemas.METASKILL["parameters"]["properties"]["skill_path"]["description"]
    assert "skill name" in description


def test_missing_skill_path_rejected():
    result = json.loads(tools.metaskill({"operation": "generalize"}))
    assert result["success"] is False


def test_not_implemented_pipeline_reports_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "metaskill.generalize", raising=False)
    monkeypatch.setattr(tools, "_load_entry_point", lambda operation: None)
    result = json.loads(tools.metaskill(
        {"operation": "generalize", "skill_path": "x"}))
    assert result["success"] is False
    assert "not implemented" in result["error"]


def test_pipeline_exception_does_not_leak_message(monkeypatch):
    def boom(skill_path, output_path):
        raise ValueError("secret-value-123")

    monkeypatch.setattr(tools, "_load_entry_point", lambda operation: boom)
    raw = tools.metaskill({"operation": "generalize", "skill_path": "x"})
    assert "secret-value-123" not in raw
    assert json.loads(raw)["success"] is False


def test_pipeline_cannot_override_operation(monkeypatch):
    monkeypatch.setattr(
        tools, "_load_entry_point", lambda operation: lambda skill_path, output_path: {
            "operation": "other"}
    )
    result = json.loads(tools.metaskill(
        {"operation": "generalize", "skill_path": "x"}))
    assert result["operation"] == "generalize"


@pytest.mark.parametrize("bad_args", [None, "not a dict", ["generalize"], 42])
def test_handler_never_raises_on_malformed_args(bad_args):
    result = json.loads(tools.metaskill(bad_args))
    assert result["success"] is False


def test_handler_never_raises_on_unexpected_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("leaky-message-123")

    monkeypatch.setattr(tools, "run", boom)
    raw = tools.metaskill({"operation": "generalize", "skill_path": "x"})
    assert "leaky-message-123" not in raw
    assert json.loads(raw)["success"] is False


class FakeCtx:
    def __init__(self, accepts=True):
        self.injected = []
        self.accepts = accepts

    def inject_message(self, content, role="user", **kwargs):
        self.injected.append(content)
        return self.accepts


def test_command_forwards_request_to_agent():
    ctx = FakeCtx()
    handler = tools.make_command_handler(ctx)
    assert handler("generalize gcp-file-transfer") is None
    assert ctx.injected == [
        "Use the metaskill tool to: generalize gcp-file-transfer"]


def test_command_without_args_shows_usage():
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)("   ")
    assert "usage:" in out
    assert ctx.injected == []


def test_command_reports_when_message_not_accepted():
    out = tools.make_command_handler(FakeCtx(accepts=False))("generalize x")
    assert "could not send" in out


def load_plugin():
    import importlib.util

    root = os.path.join(os.path.dirname(__file__), "..")
    spec = importlib.util.spec_from_file_location(
        "metaskill_plugin", os.path.join(root, "__init__.py"), submodule_search_locations=[root]
    )
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["metaskill_plugin"] = plugin
    spec.loader.exec_module(plugin)
    return plugin


class RegisteringCtx(FakeCtx):
    def __init__(self):
        super().__init__()
        self.tools, self.commands = [], []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append((name, handler, description, args_hint))


def test_register_works_without_auxiliary_tasks():
    ctx = RegisteringCtx()  # an older Hermes: no register_auxiliary_task
    load_plugin().register(ctx)
    assert [t["name"] for t in ctx.tools] == ["metaskill"]
    assert ctx.commands


def test_register_wires_tool_and_command():
    plugin = load_plugin()

    class Ctx(RegisteringCtx):
        def __init__(self):
            super().__init__()
            self.tasks = []

        def register_auxiliary_task(self, key, **kwargs):
            self.tasks.append(key)

    ctx = Ctx()
    plugin.register(ctx)
    assert [t["name"] for t in ctx.tools] == ["metaskill"]
    assert ctx.tasks == ["metaskill_judge"]
    name, handler, _, hint = ctx.commands[0]
    assert name == "metaskill" and hint
    handler("generalize my-skill")
    assert ctx.injected == ["Use the metaskill tool to: generalize my-skill"]
