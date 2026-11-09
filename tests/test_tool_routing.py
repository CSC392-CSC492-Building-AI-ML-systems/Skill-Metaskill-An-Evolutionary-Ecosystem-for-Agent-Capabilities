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
        "generalize", "personalize", "evaluate", "rank"]


def test_schema_stays_small():
    # Sent to the model every turn; guard against it growing unnoticed.
    assert len(json.dumps(schemas.METASKILL)) < 1500


def test_generalize_routes_with_default_output(fake_pipelines):
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": "skills/gcp"}))
    expected_output = os.path.join("metaskill-output", "gcp-template")
    assert fake_pipelines["generalize"] == ("skills/gcp", expected_output)
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["counts"] == {"findings": 2}
    assert result["unresolved"] == [] and result["warnings"] == []


def test_default_output_uses_directory_name_for_skill_md():
    path = tools.default_output_path("generalize", os.path.join("skills", "gcp", "SKILL.md"))
    assert path == os.path.join("metaskill-output", "gcp-template")


def test_personalize_routes_config(fake_pipelines):
    args = {"operation": "personalize", "skill_path": "out/t", "config_path": "c.json", "output_path": "o"}
    result = json.loads(tools.metaskill(args))
    assert fake_pipelines["personalize"] == ("out/t", "c.json", "o")
    assert result["operation"] == "personalize"


def test_personalize_requires_config(fake_pipelines):
    result = json.loads(tools.metaskill({"operation": "personalize", "skill_path": "out/t"}))
    assert result["success"] is False
    assert "config_path" in result["error"]
    assert "personalize" not in fake_pipelines


def test_unknown_operation_rejected():
    result = json.loads(tools.metaskill({"operation": "merge", "skill_path": "x"}))
    assert result["success"] is False


def test_skill_name_instead_of_path_tells_model_to_resolve(fake_pipelines):
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": "gcp-file-transfer"}))
    assert result["success"] is False
    assert "find the skill's directory" in result["error"]
    assert "generalize" not in fake_pipelines


def test_missing_config_file_rejected(fake_pipelines):
    args = {"operation": "personalize", "skill_path": "out/t", "config_path": "nope.json"}
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
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": "x"}))
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
        tools, "_load_entry_point", lambda operation: lambda skill_path, output_path: {"operation": "other"}
    )
    result = json.loads(tools.metaskill({"operation": "generalize", "skill_path": "x"}))
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


@pytest.fixture
def fake_evaluate(monkeypatch):
    """Stand-ins for evaluate_skill and rank_skills, recording how they were called."""
    calls = {}

    def pipeline(operation):
        def run(target, **kwargs):
            calls[operation] = (target, kwargs)
            return {"artifacts": ["report.json"], "counts": {"skills": 1}}
        return run

    module = types.ModuleType("metaskill.evaluate")
    module.evaluate_skill, module.rank_skills = pipeline("evaluate"), pipeline("rank")
    monkeypatch.setitem(sys.modules, "metaskill.evaluate", module)
    return calls


def test_evaluate_routes_with_ctx_and_no_default_output(fake_evaluate):
    ctx = object()
    result = json.loads(tools.metaskill({"operation": "evaluate", "skill_path": "skills/gcp"},
                                        ctx=ctx))
    assert fake_evaluate["evaluate"] == (
        "skills/gcp", {"config_path": None, "output_path": None, "ctx": ctx})
    assert result["success"] is True and result["operation"] == "evaluate"


def test_rank_splits_a_comma_separated_list(fake_evaluate):
    tools.metaskill({"operation": "rank", "skill_path": "skills/gcp, x"})
    assert fake_evaluate["rank"][0] == ["skills/gcp", "x"]


def test_rank_keeps_a_path_with_a_comma_whole(fake_evaluate, workspace):
    (workspace / "a,b").mkdir()
    tools.metaskill({"operation": "rank", "skill_path": "a,b"})
    assert fake_evaluate["rank"][0] == ["a,b"]


def test_evaluate_does_not_split_a_list(fake_evaluate):
    result = json.loads(tools.metaskill({"operation": "evaluate", "skill_path": "x,skills/gcp"}))
    assert result["success"] is False and "evaluate" not in fake_evaluate


def test_rank_names_the_skill_it_cannot_find(fake_evaluate):
    result = json.loads(tools.metaskill({"operation": "rank", "skill_path": "x, gcp"}))
    assert result["success"] is False and "rank" not in fake_evaluate
    assert "gcp" in result["error"] and "find the skill's directory" in result["error"]


@pytest.mark.parametrize("operation", ["evaluate", "rank"])
@pytest.mark.parametrize("skill_path", [None, ""])
def test_evaluation_without_skills_tells_the_agent_to_ask(fake_evaluate, operation, skill_path):
    result = json.loads(tools.metaskill({"operation": operation, "skill_path": skill_path}))
    assert result["success"] is False and "ask the user" in result["error"]


def test_rank_with_only_commas_has_no_skills(fake_evaluate):
    result = json.loads(tools.metaskill({"operation": "rank", "skill_path": " , "}))
    assert result["success"] is False and "ask the user" in result["error"]


def test_evaluate_takes_an_optional_config(fake_evaluate):
    tools.metaskill({"operation": "evaluate", "skill_path": "x", "config_path": "c.json"})
    assert fake_evaluate["evaluate"][1]["config_path"] == "c.json"
    result = json.loads(tools.metaskill(
        {"operation": "evaluate", "skill_path": "x", "config_path": "nope.json"}))
    assert result["success"] is False and "config_path" in result["error"]


def test_a_failed_evaluation_is_not_a_success(monkeypatch):
    module = types.ModuleType("metaskill.evaluate")
    module.evaluate_skill = lambda target, **kwargs: {"status": "error", "error": "no skill"}
    monkeypatch.setitem(sys.modules, "metaskill.evaluate", module)
    result = json.loads(tools.metaskill({"operation": "evaluate", "skill_path": "x"}))
    assert result["success"] is False and result["error"] == "no skill"


def test_evaluate_and_rank_end_to_end(workspace):
    (workspace / "x" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    args = {"operation": "rank", "skill_path": "x", "output_path": "out/reports"}
    result = json.loads(tools.metaskill(args, ctx=object(), task_id="ignored"))
    assert result["success"] is True and result["counts"]["unranked"] == 1
    assert all(os.path.exists(path) for path in result["artifacts"])


class FakeCtx:
    def __init__(self, accepts=True):
        self.injected = []
        self.accepts = accepts

    def inject_message(self, content, role="user", **kwargs):
        self.injected.append(content)
        return self.accepts


def test_command_forwards_other_requests_and_says_so():
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)("compare my skills")
    assert ctx.injected == ["Use the metaskill tool to: compare my skills"]
    assert "sent to the agent" in out


def test_command_without_args_shows_usage():
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)("   ")
    assert "usage:" in out
    assert ctx.injected == []


def test_command_reports_when_message_not_accepted():
    out = tools.make_command_handler(FakeCtx(accepts=False))("compare my skills")
    assert "could not send" in out and "allow_gateway_injection" in out and "usage:" in out


def test_command_runs_a_path_itself_and_prints_the_result(fake_evaluate):
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)("evaluate skills/gcp")
    assert ctx.injected == []
    assert fake_evaluate["evaluate"] == (
        "skills/gcp", {"config_path": None, "output_path": None, "ctx": ctx})
    assert "report.json" in out


def test_command_splits_a_rank_list_and_reads_config(fake_evaluate):
    tools.make_command_handler(FakeCtx())('rank x, "skills/gcp" --config c.json')
    target, kwargs = fake_evaluate["rank"]
    assert target == ["x", "skills/gcp"] and kwargs["config_path"] == "c.json"


@pytest.mark.parametrize("request_text", ["evaluate my skills", 'evaluate "my skills"'])
def test_command_takes_a_path_with_spaces(fake_evaluate, workspace, request_text):
    (workspace / "my skills").mkdir()
    tools.make_command_handler(FakeCtx())(request_text)
    assert fake_evaluate["evaluate"][0] == "my skills"


@pytest.fixture
def installed(monkeypatch, workspace):
    """Two installed skills in different categories, and one name they share."""
    folders = {}
    for category, name, also in (("devops", "deploy", "ship"), ("web", "crawl", "ship")):
        folder = workspace / "home" / category / name
        folder.mkdir(parents=True)
        folders[name] = str(folder)
        names = {name, f"{category}/{name}", also}
        folders.setdefault("all", []).append((str(folder), f"{category}/{name}", names))
    monkeypatch.setattr(tools, "_installed_skills", lambda: folders["all"])
    return folders


@pytest.mark.parametrize("name", ["deploy", "DEPLOY", "devops/deploy", "devops\\deploy"])
def test_command_finds_an_installed_skill_by_name(fake_evaluate, installed, name):
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)(f"evaluate {name}")
    assert fake_evaluate["evaluate"][0] == installed["deploy"]
    assert ctx.injected == [] and "report.json" in out


def test_command_ranks_a_mix_of_names_and_paths(fake_evaluate, installed):
    tools.make_command_handler(FakeCtx())("rank deploy, skills/gcp, web/crawl")
    assert fake_evaluate["rank"][0] == [installed["deploy"], "skills/gcp", installed["crawl"]]


def test_a_path_wins_over_a_name(fake_evaluate, installed, workspace):
    (workspace / "deploy").mkdir()
    tools.make_command_handler(FakeCtx())("evaluate deploy")
    assert fake_evaluate["evaluate"][0] == "deploy"


def test_a_name_with_several_skills_lists_them_and_runs_nothing(fake_evaluate, installed):
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)("evaluate ship")
    assert "ship matches 2 skills: devops/deploy, web/crawl" in out
    assert tools.SKILL_HELP in out
    assert fake_evaluate == {} and ctx.injected == []


@pytest.mark.parametrize("request_text", ["evaluate nothing-here", "rank my skills",
                                          "rank deploy, nothing-here"])
def test_an_unknown_skill_says_how_to_name_one_and_runs_nothing(fake_evaluate, installed,
                                                                request_text):
    ctx = FakeCtx()
    out = tools.make_command_handler(ctx)(request_text)
    assert "No skill found for" in out and tools.SKILL_HELP in out
    assert fake_evaluate == {} and ctx.injected == []


@pytest.mark.parametrize("request_text", ["evaluate", "rank", "rank ,"])
def test_an_operation_without_skills_says_how_to_name_one(fake_evaluate, request_text):
    ctx = FakeCtx()
    assert tools.SKILL_HELP in tools.make_command_handler(ctx)(request_text)
    assert fake_evaluate == {} and ctx.injected == []


def test_usage_says_how_to_name_a_skill():
    assert tools.SKILL_HELP in tools.make_command_handler(FakeCtx())("")


@pytest.fixture
def hermes_skills(monkeypatch, workspace):
    """A stand-in for Hermes' agent.skill_utils over one skills folder."""
    root = workspace / "home"
    utils = types.ModuleType("agent.skill_utils")
    utils.get_all_skills_dirs = lambda: [root]
    utils.iter_skill_index_files = lambda folder, name: sorted(folder.rglob(name))
    utils.parse_frontmatter = lambda text: ({"name": text.strip()} if text.strip() else {}, text)
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.skill_utils", utils)
    return root


def test_installed_skills_go_by_folder_category_and_frontmatter_name(hermes_skills):
    folder = hermes_skills / "devops" / "deploy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("Ship-It", encoding="utf-8")
    assert tools._installed_skills() == [
        (str(folder), "devops/deploy", {"deploy", "devops/deploy", "ship-it"})]


def test_an_unreadable_skill_is_still_found_by_its_folder(hermes_skills):
    folder = hermes_skills / "devops" / "deploy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(b"\xff\xfe not utf-8")
    assert tools._installed_skills() == [(str(folder), "devops/deploy", {"deploy", "devops/deploy"})]


def test_there_are_no_names_outside_hermes(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.skill_utils", None)
    assert tools._installed_skills() == []
    assert "No skill found" in tools.make_command_handler(FakeCtx())("evaluate deploy")


def test_command_prints_a_failure(monkeypatch):
    module = types.ModuleType("metaskill.evaluate")
    module.evaluate_skill = lambda target, **kwargs: {
        "status": "error", "error": "no skill could be evaluated",
        "warnings": ["x could not be evaluated: ImportError"]}
    monkeypatch.setitem(sys.modules, "metaskill.evaluate", module)
    out = tools.make_command_handler(FakeCtx())("evaluate x")
    assert "evaluate failed: no skill could be evaluated" in out
    assert "x could not be evaluated: ImportError" in out


def test_command_prints_the_table_and_what_is_missing(monkeypatch):
    module = types.ModuleType("metaskill.evaluate")
    module.evaluate_skill = lambda target, **kwargs: {
        "table": "| Rank |", "warnings": ["x could not be evaluated"],
        "partial": ["Partial result"], "dependencies": [
            {"package": "scanner", "install_command": "pip install scanner", "install_url": ""}]}
    monkeypatch.setitem(sys.modules, "metaskill.evaluate", module)
    out = tools.make_command_handler(FakeCtx())("evaluate x").split("\n")
    assert out == [f"Scanned x in {os.getcwd()}", "Rank", "----",
                   "Warning: x could not be evaluated",
                   "Not installed: scanner (pip install scanner)"]


TABLE = (
    "| Rank | Skill | Overall | Safety |\n"
    "|---|---|---|---|\n"
    "| - | claude-code | - | 0.30 |\n"
    "| 1 | a\\|b \\<x\\> | 0.90 | 1.00 |\n"
    "\n"
    "Notes:\n"
    "- No skill is ranked: no score for clarity.\n"
)


def test_the_table_is_printed_as_lined_up_columns():
    assert tools._plain(TABLE).split("\n") == [
        "Rank  Skill        Overall  Safety",
        "----  -----------  -------  ------",
        "-     claude-code  -        0.30",
        "1     a|b <x>      0.90     1.00",
        "",
        "Notes:",
        "- No skill is ranked: no score for clarity.",
        "",
    ]


def test_control_characters_in_the_table_are_not_printed():
    assert "\x1b" not in tools._plain("| a\x1b[31m |\n|---|\n| b |")


def test_files_written_to_one_folder_are_one_line():
    artifacts = [os.path.join("out", "run", "ranked-evaluation.json"),
                 os.path.join("out", "run", "ranked-evaluation.md")]
    assert tools._saved(artifacts) == [
        f"Saved ranked-evaluation.json, ranked-evaluation.md to {os.path.join('out', 'run')}"]


def test_the_summary_says_which_skills_ran_and_where(workspace):
    assert tools._scanned([str(workspace / "x")]) == f"Scanned x in {workspace}"
    two_folders = [str(workspace / "x"), str(workspace / "y"), str(workspace / "skills" / "gcp")]
    assert tools._scanned(two_folders).split("\n") == [
        f"Scanned x, y in {workspace}", f"Scanned gcp in {workspace / 'skills'}"]
    many = [str(workspace / f"c{i}" / "s") for i in range(4)]
    assert tools._scanned(many) == f"Scanned 4 skills in {workspace}"


def test_a_failure_does_not_say_what_was_scanned(monkeypatch):
    module = types.ModuleType("metaskill.evaluate")
    module.evaluate_skill = lambda target, **kwargs: {"status": "error", "error": "bad config"}
    monkeypatch.setitem(sys.modules, "metaskill.evaluate", module)
    out = tools.make_command_handler(FakeCtx())("evaluate x")
    assert out == "metaskill evaluate failed: bad config"


def test_command_does_not_leak_or_raise_on_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("leaky-message-123")

    monkeypatch.setattr(tools, "run", boom)
    assert tools.make_command_handler(FakeCtx())("evaluate x") == "metaskill failed: RuntimeError"


class SessionCtx(FakeCtx):
    """A session whose folder is not the process's own, as in the Hermes gateway."""

    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd

    def session_cwd(self):
        return str(self.cwd)


@pytest.fixture
def gateway_cwd(tmp_path_factory, monkeypatch):
    """The process runs somewhere else, so a path only resolves through the session."""
    folder = tmp_path_factory.mktemp("gateway")
    monkeypatch.chdir(folder)
    return folder


def in_session(workspace, *parts):
    return os.path.join(str(workspace), *parts)


def test_relative_paths_mean_the_session_folder(fake_evaluate, workspace, gateway_cwd):
    args = {"operation": "evaluate", "skill_path": "skills/gcp", "config_path": "c.json",
            "output_path": "out/t"}
    result = json.loads(tools.metaskill(args, ctx=SessionCtx(workspace)))
    assert result["success"] is True
    target, kwargs = fake_evaluate["evaluate"]
    assert target == in_session(workspace, "skills/gcp")
    assert kwargs["config_path"] == in_session(workspace, "c.json")
    assert kwargs["output_path"] == in_session(workspace, "out/t")


def test_an_absolute_path_is_left_alone(fake_evaluate, workspace, gateway_cwd):
    path = in_session(workspace, "skills", "gcp")
    tools.metaskill({"operation": "evaluate", "skill_path": path}, ctx=SessionCtx(gateway_cwd))
    assert fake_evaluate["evaluate"][0] == path


def test_rank_list_means_the_session_folder(fake_evaluate, workspace, gateway_cwd):
    tools.metaskill({"operation": "rank", "skill_path": "x, skills/gcp"},
                    ctx=SessionCtx(workspace))
    assert fake_evaluate["rank"][0] == [in_session(workspace, "x"),
                                        in_session(workspace, "skills/gcp")]


def test_default_generalize_output_is_in_the_session_folder(fake_pipelines, workspace, gateway_cwd):
    tools.metaskill({"operation": "generalize", "skill_path": "skills/gcp"},
                    ctx=SessionCtx(workspace))
    assert fake_pipelines["generalize"][1] == in_session(
        workspace, "metaskill-output", "gcp-template")


def test_command_finds_a_relative_path_in_the_session_folder(fake_evaluate, workspace, gateway_cwd):
    ctx = SessionCtx(workspace)
    tools.make_command_handler(ctx)("evaluate ./skills/gcp")
    assert ctx.injected == []
    assert fake_evaluate["evaluate"][0] == in_session(workspace, "./skills/gcp")


def test_reports_land_in_the_session_folder(workspace, gateway_cwd):
    (workspace / "x" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    out = tools.make_command_handler(SessionCtx(workspace))("evaluate x")
    reports = list((workspace / "evaluation-reports").rglob("ranked-evaluation.json"))
    assert len(reports) == 1
    assert f"Saved ranked-evaluation.json, ranked-evaluation.md to {reports[0].parent}" in out
    assert "|" not in out  # the table is printed as columns
    assert list(gateway_cwd.iterdir()) == []


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
    handler("compare my-skill")
    assert ctx.injected == ["Use the metaskill tool to: compare my-skill"]


# --- /metaskill skills, suggestions and the folder -------------------------------------


def skill_at(root, *parts):
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    return folder


def test_local_skills_are_found_below_the_folder_but_not_inside_a_skill(workspace):
    skill_at(workspace, "fixtures", "clean")
    skill_at(workspace, "fixtures", "clean", "examples", "nested")  # part of clean
    skill_at(workspace, ".hidden", "secret")
    skill_at(workspace, "node_modules", "pkg")
    skill_at(workspace, "a", "b", "c", "d", "e", "too-deep")
    assert tools._local_skills(str(workspace)) == (["fixtures/clean"], False)


def test_local_search_stops_in_a_large_folder(workspace, monkeypatch):
    monkeypatch.setattr(tools, "_SEARCH_LIMIT", 3)
    for name in "abcdef":
        skill_at(workspace, "many", name)
    found, stopped = tools._local_skills(str(workspace))
    assert stopped and len(found) < 6


def test_skills_lists_the_folder_and_what_is_under_it_and_installed(workspace, installed):
    skill_at(workspace, "fixtures", "clean")
    skill_at(workspace, "fixtures", "destructive")
    out = tools.make_command_handler(SessionCtx(workspace))("skills").split("\n")
    assert out[0] == f"Folder: {workspace} (relative paths start here)"
    assert "Skills under this folder (2):" in out
    assert "  fixtures: clean, destructive" in out
    assert "Installed skills (2):" in out
    assert "  devops: deploy" in out and "  web: crawl" in out


def test_skills_narrows_the_list(workspace, installed):
    skill_at(workspace, "fixtures", "clean")
    out = tools.make_command_handler(SessionCtx(workspace))("skills dep")
    assert "devops: deploy" in out and "crawl" not in out and "clean" not in out
    nothing = tools.make_command_handler(SessionCtx(workspace))("skills zzz")
    assert "No skills match zzz." in nothing


def test_a_long_list_is_cut(workspace, monkeypatch):
    monkeypatch.setattr(tools, "_installed_skills", lambda: [])
    for i in range(tools._LISTED_GROUPS + 2):
        skill_at(workspace, f"group{i:02}", "s")
    out = tools.make_command_handler(SessionCtx(workspace))("skills")
    assert "  and 2 more folders" in out


def test_a_missing_name_gets_suggestions(fake_evaluate, installed, workspace):
    skill_at(workspace, "fixtures", "clean")
    ctx = SessionCtx(workspace)
    out = tools.make_command_handler(ctx)("evaluate deplyo")
    assert "No skill found for deplyo. Did you mean devops/deploy?" in out
    assert "fixtures/clean" in tools.make_command_handler(ctx)("evaluate claen")
    assert fake_evaluate == {}


def test_suggestions_take_a_part_of_the_name():
    installed = [("f", "autonomous-ai-agents/claude-code", {"claude-code"})]
    assert tools._suggestions("claude", installed, []) == ["autonomous-ai-agents/claude-code"]
    assert tools._suggestions("zzz", installed, []) == []


@pytest.mark.parametrize("request_text", ["", "evaluate", "evaluate nothing-here"])
def test_help_says_which_folder_paths_start_from(workspace, installed, request_text):
    out = tools.make_command_handler(SessionCtx(workspace))(request_text)
    assert f"Folder: {workspace} (relative paths start here)" in out
