"""HermesAgent (docs/evaluate/hermes_agent.md).

The unit tests need neither Hermes nor Docker: a fake child process stands in for the
Hermes session. The integration tests at the end run the real thing and are opt-in:

    METASKILL_HERMES_IT=1    Hermes + Docker, no model calls (the check and its guards)
    METASKILL_HERMES_LIVE=1  also one real session per variant on the configured model
"""

import ast
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from metaskill.evaluate import hermes_agent, hermes_child
from metaskill.evaluate.hermes_agent import (
    CHILD, HermesAgent, HermesAgentUnavailable, skill_message)
from metaskill.evaluate.judge import JudgeError
from metaskill.evaluate.models import EvaluationConfig, Skill
from metaskill.evaluate.runner import run_skill

OK_RESULT = {
    "status": "ok", "final_response": "60", "transcript": "[user] hi",
    "usage": {"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 5,
              "cache_read_tokens": 300, "cache_write_tokens": 0, "api_calls": 4},
    "tool_calls": 3,
}


@pytest.fixture(autouse=True)
def _not_windows(monkeypatch):
    # HermesAgent refuses Windows outright. These tests drive the adapter itself (with a fake
    # child, or the integration tests), so they run as on Linux; the refusal has its own test.
    monkeypatch.setattr(hermes_agent, "_windows", lambda: False)


def make_skill(tmp_path, files=None):
    folder = tmp_path / "csv-total"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# CSV total\n\nRun scripts/total.sh.\n")
    for rel, text in (files or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    return Skill(path=folder, name="csv-total", body=(folder / "SKILL.md").read_text(),
                 files=[Path(rel) for rel in (files or {})])


def docker_workspace(name="metaskill-run-1"):
    return SimpleNamespace(name=name, sandbox=SimpleNamespace(docker="/usr/bin/docker"),
                           root="/work", skill_dir="/tmp/skill")


def fake_agent(tmp_path, script: str, **kwargs):
    """A HermesAgent whose child is `script` (Python), reading the request path in argv[1]."""
    child = tmp_path / "fake_child.py"
    child.write_text("import json, sys, os, time, subprocess\n"
                     "request = json.load(open(sys.argv[1]))\n"
                     "out = request['result']\n" + script)
    agent = HermesAgent(**kwargs)
    agent._argv = [sys.executable, str(child)]
    return agent


def writes(result: dict, seen: Path | None = None) -> str:
    """Fake child body: optionally save the request it got to `seen`, then write `result`."""
    save = f"json.dump(request, open({str(seen)!r}, 'w'))\n" if seen else ""
    return save + f"json.dump({result!r}, open(out, 'w'))\n"


# --- The skill message -------------------------------------------------------------------


def test_skill_message_points_at_the_sandbox_copy_not_the_host(tmp_path):
    skill = make_skill(tmp_path, {"scripts/total.sh": "echo 60\n"})
    message = skill_message(skill, docker_workspace())
    assert '"csv-total" skill preloaded' in message
    assert "Run scripts/total.sh." in message
    assert "[Skill directory: /tmp/skill]" in message
    assert "- scripts/total.sh" in message
    assert str(tmp_path) not in message  # no host path reaches the model
    assert "skill_view" not in message  # Hermes' tool for it reads the host


def test_skill_message_leaves_out_links_the_runner_does_not_copy(tmp_path):
    skill = make_skill(tmp_path, {"scripts/total.sh": "echo 60\n"})
    try:
        os.symlink("/etc/hostname", skill.path / "link.txt")
    except OSError:
        pytest.skip("cannot create symlinks here")
    skill.files.append(Path("link.txt"))
    message = skill_message(skill)
    assert "- scripts/total.sh" in message and "link.txt" not in message


def test_skill_message_lists_exactly_the_files_the_runner_copies(tmp_path):
    # One rule (runner.copied) for the copy and the list, so the agent is never pointed at a
    # file that is not in the sandbox: hard links and paths outside the skill too.
    skill = make_skill(tmp_path, {"scripts/total.sh": "echo 60\n"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.env").write_text("KEY=x", encoding="utf-8")
    try:
        (skill.path / "linked-dir").symlink_to(outside, target_is_directory=True)
        os.link(outside / "secret.env", skill.path / "hard.md")
    except OSError:
        pytest.skip("cannot create links here")
    skill.files += [Path("linked-dir/secret.env"), Path("hard.md")]
    message = skill_message(skill)
    assert "- scripts/total.sh" in message
    assert "secret.env" not in message and "hard.md" not in message


def test_the_sessions_own_seconds_reach_the_run(tmp_path):
    run = fake_agent(tmp_path, writes({**OK_RESULT, "seconds": 4.5})).run("p", None, docker_workspace())
    assert run.seconds == 4.5
    assert fake_agent(tmp_path, writes(OK_RESULT)).run("p", None, docker_workspace()).seconds is None
    assert fake_agent(tmp_path, writes({**OK_RESULT, "seconds": "soon"})).run("p", None, docker_workspace()).seconds is None


def test_a_skill_without_bundled_files_has_no_file_list(tmp_path):
    assert "supporting files" not in skill_message(make_skill(tmp_path))


# --- Finding Hermes' runtime ----------------------------------------------------------------

BOOT = ("import os, sys, runpy; sys.path.insert(0, '/h'); import hermes_bootstrap; "
        "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)")


def runtime_reply(monkeypatch, stdout="", error=None):
    def fake_run(argv, **kwargs):
        assert argv[1:] == ["--print-runtime-command"]
        if error:
            raise error
        return SimpleNamespace(stdout=stdout, returncode=0)
    monkeypatch.setattr(hermes_agent.subprocess, "run", fake_run)


def test_runtime_reuses_hermes_bootstrap_and_runs_the_child(monkeypatch):
    runtime_reply(monkeypatch, json.dumps(["/py3", "-I", "-c", BOOT]))
    argv = HermesAgent()._runtime_argv()
    assert argv[:3] == ["/py3", "-I", "-c"]
    assert argv[3].startswith("import os, sys, runpy; sys.path.insert(0, '/h'); import hermes_bootstrap; ")
    assert argv[3].endswith(f"runpy.run_path({str(CHILD)!r}, run_name='__main__')")
    assert "hermes_cli.main" not in argv[3]


@pytest.mark.parametrize("stdout", [
    "not json", json.dumps({"a": 1}), json.dumps(["/py3", "-c"]),
    json.dumps(["/py3", "-I", "-c", "import x; run_something_else()"]),
    json.dumps(["/py3", "-I", "-x", BOOT]),
])
def test_an_unexpected_runtime_is_unavailable_not_guessed(monkeypatch, stdout):
    runtime_reply(monkeypatch, stdout)
    with pytest.raises(HermesAgentUnavailable):
        HermesAgent()._runtime_argv()


def test_missing_hermes_is_unavailable(monkeypatch):
    runtime_reply(monkeypatch, error=FileNotFoundError("hermes"))
    with pytest.raises(HermesAgentUnavailable, match="--print-runtime-command"):
        HermesAgent()._runtime_argv()


# --- One run -------------------------------------------------------------------------------


def test_a_finished_session_becomes_an_agent_run(tmp_path):
    run = fake_agent(tmp_path, writes(OK_RESULT)).run("total it", None, docker_workspace())
    assert run.output == "60" and run.transcript == "[user] hi"
    assert (run.input_tokens, run.output_tokens, run.reasoning_tokens) == (100, 20, 5)
    assert (run.cache_read_tokens, run.llm_calls, run.tool_calls) == (300, 4, 3)


def test_the_request_names_the_container_and_carries_the_skill_only_with_it(tmp_path):
    skill = make_skill(tmp_path)
    seen = tmp_path / "seen.json"
    agent = fake_agent(tmp_path, writes(OK_RESULT, seen), model="m", max_iterations=7)
    for variant_skill in (None, skill):
        agent.run("total it", variant_skill, docker_workspace("box-7"))
        request = json.loads(seen.read_text())
        assert request["protocol"] == hermes_child.PROTOCOL == hermes_agent.PROTOCOL
        assert (request["container"], request["docker"], request["workdir"]) == \
            ("box-7", "/usr/bin/docker", "/work")
        assert (request["model"], request["max_iterations"], request["prompt"]) == ("m", 7, "total it")
        if variant_skill is None:
            assert request["skill_message"] is None
        else:
            assert "[Skill directory: /tmp/skill]" in request["skill_message"]


def test_a_setup_error_is_unavailable_and_a_judge_error(tmp_path):
    agent = fake_agent(tmp_path, writes({"status": "setup_error", "error": "resolves to 'local'"}))
    with pytest.raises(HermesAgentUnavailable, match="resolves to 'local'") as e:
        agent.run("p", None, docker_workspace())
    assert isinstance(e.value, JudgeError)  # the runner records it as an error, not a failed run


def test_a_provider_failure_is_unavailable_not_a_judged_run(tmp_path):
    # Hermes puts its own error text in final_response when the provider cannot serve the
    # session; the child reports it as provider_error, so that text is never judged.
    agent = fake_agent(tmp_path, writes({"status": "provider_error", "failure_reason": "model_not_found",
                                         "error": "Model 'x' isn't available"}))
    with pytest.raises(HermesAgentUnavailable, match=r"provider.*\(model_not_found\).*isn't available"):
        agent.run("p", None, docker_workspace())


def test_an_agent_error_is_an_ordinary_failure(tmp_path):
    agent = fake_agent(tmp_path, writes({"status": "agent_error", "error": "APIError: 500"}))
    with pytest.raises(RuntimeError, match="APIError") as e:
        agent.run("p", None, docker_workspace())
    assert not isinstance(e.value, JudgeError)


def test_an_agent_error_names_why_the_session_failed(tmp_path):
    agent = fake_agent(tmp_path, writes({"status": "agent_error", "failure_reason": "context_overflow",
                                         "error": "context window exceeded"}))
    with pytest.raises(RuntimeError, match=r"session failed \(context_overflow\): context window"):
        agent.run("p", None, docker_workspace())


@pytest.mark.parametrize("reason", sorted(hermes_child.TASK_FAILURES))
def test_a_failure_the_task_caused_is_a_failed_run(reason):
    # A skill can drive these (it floods the context, or its text is blocked), so dropping
    # the run as an environment error would let such a skill go unscored.
    assert hermes_child.failure_status({"failed": True, "failure_reason": reason}) == "agent_error"
    assert hermes_child.failure_status({"failed": True, "turn_exit_reason": reason}) == "agent_error"


@pytest.mark.parametrize("reason", [
    "rate_limit", "billing", "billing_unverified", "auth", "model_not_found", "model_entitlement",
    "server_error", "empty_response", "loop_error", "session_busy", "interpreter_shutdown",
    "a-reason-hermes-adds-later", None])
def test_any_other_failure_is_the_environment_not_the_skill(reason):
    # Unnamed reasons stay unscored: a new Hermes reason must not count against a skill by accident.
    assert hermes_child.failure_status({"failed": True, "failure_reason": reason}) == "provider_error"


def test_the_two_reason_fields_are_both_read():
    result = {"failed": True, "failure_reason": "server_error", "turn_exit_reason": "context_compression_timeout"}
    assert hermes_child.failure_status(result) == "agent_error"


# --- The pre-flight check ------------------------------------------------------------------

GOOD_CHECK = {
    "write_file": json.dumps({"bytes_written": 24, "resolved_path": "/work/check.txt"}),
    "terminal": json.dumps({"output": "/work\nnobody\nwritten by the file tool", "exit_code": 0, "error": None}),
    "read_file": json.dumps({"content": "1|from-terminal", "not_found": False}),
    "guards": {"local backend": "refused: no", "docker backend": "refused: no"},
}


def broken(**changes):
    return {**GOOD_CHECK, **changes}


def test_a_working_check_has_no_problems():
    assert hermes_agent.check_problems(GOOD_CHECK, "/work") == []


@pytest.mark.parametrize("check, expected", [
    # Docker stopped: every tool fails and Hermes' setup still succeeded.
    (broken(terminal=json.dumps({"output": "", "exit_code": 126, "error": "failed to connect to the docker API"}),
            write_file=json.dumps({"error": "failed to connect to the docker API"}),
            read_file=json.dumps({"error": "failed to connect", "not_found": True})),
     ["write_file reported an error", "terminal reported an error", "read_file reported an error"]),
    # A Hermes whose tools work in another folder (/root, not /work).
    (broken(write_file=json.dumps({"resolved_path": "/root/check.txt"})), ["write_file did not write into /work"]),
    (broken(terminal=json.dumps({"output": "/root\nnobody", "exit_code": 0})), ["terminal did not run in /work"]),
    (broken(terminal=json.dumps({"output": "/work\n", "exit_code": 1})), ["terminal did not run in /work"]),
    (broken(read_file=json.dumps({"content": "", "not_found": True})), ["read_file did not find"]),
    (broken(guards={"local backend": "created a local backend"}), ["a guard did not refuse"]),
    (broken(guards={}), ["a guard did not refuse"]),
    (broken(terminal="not json"), ["terminal gave no readable answer"]),
    (broken(read_file=None), ["read_file gave no readable answer"]),
    (broken(write_file="[1, 2]"), ["write_file gave no readable answer"]),
    ("nothing", ["no check result"]),
])
def test_each_way_the_tools_can_fail_is_reported(check, expected):
    problems = hermes_agent.check_problems(check, "/work")
    assert len(problems) >= len(expected)
    for reason in expected:
        assert any(reason in p for p in problems), (reason, problems)


@pytest.mark.parametrize("empty, expected", [
    ({"write_file": "{}"}, "write_file did not write into /work"),
    ({"terminal": "{}"}, "terminal did not run in /work"),
    ({"read_file": "{}"}, "read_file did not find"),
    ({"write_file": "{}", "terminal": "{}", "read_file": "{}"}, "write_file did not write into /work"),
])
def test_an_empty_answer_is_not_a_success(empty, expected):
    # A tool that answers {} wrote, ran and read nothing: the success fields must be there.
    problems = hermes_agent.check_problems(broken(**empty), "/work")
    assert any(expected in p for p in problems), problems
    if len(empty) == 3:
        assert len(problems) == 3


def test_check_is_unavailable_when_the_tools_failed_even_though_setup_succeeded(tmp_path):
    result = {"status": "ok", "check": broken(terminal=json.dumps({"output": "", "exit_code": 126}))}
    with pytest.raises(HermesAgentUnavailable, match="tools did not work in the sandbox.*terminal did not run"):
        fake_agent(tmp_path, writes(result)).check(docker_workspace())


def test_check_returns_the_result_when_the_tools_worked(tmp_path):
    result = {"status": "ok", "check": GOOD_CHECK, "hermes_version": "9.9"}
    assert fake_agent(tmp_path, writes(result)).check(docker_workspace())["hermes_version"] == "9.9"


def test_windows_is_refused_before_anything_starts(tmp_path, monkeypatch):
    # No process group to kill on a timeout there, so the child could outlive it and keep calling
    # the model. Refused up front: no child is started, no model is called.
    monkeypatch.setattr(hermes_agent, "_windows", lambda: True)
    marker = tmp_path / "started"
    agent = fake_agent(tmp_path, f"open({str(marker)!r}, 'w').close()\n")
    with pytest.raises(HermesAgentUnavailable, match="does not support Windows yet") as e:
        agent.run("p", None, docker_workspace())
    assert isinstance(e.value, JudgeError)  # the runner records it as an error, not a failed run
    with pytest.raises(HermesAgentUnavailable, match="does not support Windows yet"):
        agent.check(docker_workspace())
    assert not marker.exists()


def test_a_child_that_dies_without_a_result_is_unavailable(tmp_path):
    agent = fake_agent(tmp_path, "sys.stderr.write('ImportError: no module named run_agent')\nsys.exit(3)\n")
    with pytest.raises(HermesAgentUnavailable, match="exited 3.*run_agent"):
        agent.run("p", None, docker_workspace())


@pytest.mark.parametrize("body", ["open(out, 'w').write('{not json')\n",
                                  "json.dump([1, 2], open(out, 'w'))\n"])
def test_an_unreadable_result_is_unavailable_not_a_failed_run(tmp_path, body):
    with pytest.raises(HermesAgentUnavailable, match="result"):
        fake_agent(tmp_path, body).run("p", None, docker_workspace())


def test_unmeasured_tool_calls_stay_none(tmp_path):
    result = dict(OK_RESULT, tool_calls=None)
    assert fake_agent(tmp_path, writes(result)).run("p", None, docker_workspace()).tool_calls is None


def test_a_session_past_its_timeout_is_stopped_with_everything_it_started(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    agent = fake_agent(tmp_path, f"p = subprocess.Popen(['sleep', '60'])\n"
                                 f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
                                 "time.sleep(60)\n", timeout=1.5)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        agent.run("p", None, docker_workspace())
    assert time.monotonic() - start < 10
    pid = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):  # the process group was killed, grandchild too
        os.kill(pid, 0)


def test_a_workspace_that_is_not_docker_is_unavailable():
    with pytest.raises(HermesAgentUnavailable, match="Docker sandbox"):
        HermesAgent().run("p", None, SimpleNamespace(root="/work", skill_dir="/tmp/skill"))


def test_the_runner_records_unavailable_as_an_error_not_a_failed_run(tmp_path):
    from test_runner import FakeSandbox  # its workspaces are folders, not containers

    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"tasks": [{
        "task_id": "t", "description": "d", "prompt": "p",
        "checks": [{"judge": "deterministic", "kind": "exit_code", "target": "true", "expected": "0"}],
    }]}))
    result = run_skill(make_skill(tmp_path), tasks, HermesAgent(),
                       config=EvaluationConfig(runs_per_task=1), run_dir=tmp_path / "run",
                       sandbox=FakeSandbox())
    assert result.tests == []
    assert len(result.errors) == 2 and all("HermesAgentUnavailable" in e for e in result.errors)


# --- The child's own helpers (no Hermes needed) ----------------------------------------------


def test_child_imports_nothing_but_the_standard_library_at_the_top():
    # It runs on Hermes' interpreter, where metaskill is not importable, and the tests'
    # interpreter cannot import Hermes: Hermes is imported inside functions only.
    tree = ast.parse(CHILD.read_text())
    top = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
           for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
           for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert top <= {"__future__", "json", "os", "sys", "time", "pathlib", "typing"}


def test_docker_argv_runs_bash_in_the_runs_container_under_its_own_timeout():
    argv = hermes_child.docker_argv("docker", "box", "/work", "ls", login=False, timeout=30, stdin=False)
    assert argv == ["docker", "exec", "--workdir", "/work", "box",
                    "timeout", "-s", "KILL", "35", "bash", "-c", "ls"]
    argv = hermes_child.docker_argv("docker", "box", "/work", "ls", login=True, timeout=30, stdin=True)
    assert argv[:3] == ["docker", "exec", "-i"] and argv[-4:] == ["bash", "-l", "-c", "ls"]


def test_tool_calls_are_counted_from_the_models_messages():
    messages = [{"role": "user", "content": "x"},
                {"role": "assistant", "tool_calls": [{"function": {"name": "terminal"}}] * 2},
                {"role": "tool", "content": "ok"},
                {"role": "assistant", "content": "done", "tool_calls": None}]
    assert hermes_child.count_tool_calls(messages) == 2


def test_the_transcript_shows_calls_and_clips_long_results():
    messages = [{"role": "user", "content": "total it"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "terminal", "arguments": '{"command": "ls"}'}}]},
                {"role": "tool", "name": "terminal", "content": "y" * 10_000},
                {"role": "assistant", "content": "60"}]
    text = hermes_child.transcript(messages, header="Hermes 1")
    assert text.splitlines()[0] == "Hermes 1"
    assert '[tool call: terminal] {"command": "ls"}' in text
    assert "more chars]" in text and len(text) < 3_000
    assert text.rstrip().endswith("[assistant] 60")
    huge = [{"role": "tool", "content": "z" * 5_000}] * 200
    assert len(hermes_child.transcript(huge)) <= hermes_child.MAX_TRANSCRIPT_CHARS + 50


def test_tools_beyond_the_allowed_set_are_reported_by_name():
    allowed = {"terminal", "read_file"}
    defs = [{"function": {"name": "terminal"}}, {"function": {"name": "read_file"}}]
    assert hermes_child.unexpected_tools(defs, allowed) == []
    bridge = [{"function": {"name": n}} for n in ("tool_call", "tool_search", "terminal")]
    assert hermes_child.unexpected_tools(bridge, allowed) == ["tool_call", "tool_search"]


@pytest.mark.parametrize("shape", [{"name": "terminal"}, {"function": "terminal"}, {"function": {}}, "terminal"])
def test_a_tool_definition_that_cannot_be_read_counts_as_unexpected(shape):
    # A changed Hermes shape must fail closed, not pass as "nothing unexpected".
    assert hermes_child.unexpected_tools([shape], {"terminal"}) != []


def test_tool_search_is_turned_off_for_the_session(monkeypatch):
    # Hermes puts a bridge in front of the tools that reaches connected apps
    # and MCP installs; the session must not have it.
    module = SimpleNamespace(
        ToolSearchConfig=SimpleNamespace(from_raw=lambda raw: SimpleNamespace(enabled=raw["enabled"])),
        load_config=lambda: SimpleNamespace(enabled="auto"),
        load_config_readonly=lambda: SimpleNamespace(enabled="auto"))
    monkeypatch.setitem(sys.modules, "tools", SimpleNamespace(tool_search=module))
    hermes_child._turn_off_tool_search()
    assert module.load_config().enabled == module.load_config_readonly().enabled == "off"


def test_older_hermes_without_tool_search_needs_nothing_turned_off(monkeypatch):
    monkeypatch.setitem(sys.modules, "tools", None)  # `from tools import ...` raises ImportError
    hermes_child._turn_off_tool_search()


def test_the_configured_model_is_used_when_none_is_named():
    cfg = {"model": {"default": "cfg-model", "provider": "cfg-provider"}}
    choice = hermes_child._resolve_model_and_provider(cfg, None, None)
    assert (choice.model, choice.provider) == ("cfg-model", None)  # provider stays the configured one
    assert hermes_child._resolve_model_and_provider({"model": "plain"}, "", "").model == "plain"


def test_a_named_model_and_provider_are_used_as_given():
    choice = hermes_child._resolve_model_and_provider({"model": {"default": "cfg"}}, " m1 ", " p1 ")
    assert (choice.model, choice.provider) == ("m1", "p1")


def test_a_request_from_another_version_is_refused_before_hermes_is_touched(tmp_path):
    request = {"protocol": 999, "result": str(tmp_path / "result.json")}
    (tmp_path / "request.json").write_text(json.dumps(request))
    assert hermes_child.main(["child", str(tmp_path / "request.json")]) == 0
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "setup_error" and "protocol" in result["error"]


# --- Integration (opt-in) ------------------------------------------------------------------

needs_hermes = pytest.mark.skipif(not os.environ.get("METASKILL_HERMES_IT")
                                  and not os.environ.get("METASKILL_HERMES_LIVE"),
                                  reason="set METASKILL_HERMES_IT=1 (Hermes + Docker)")


@pytest.fixture
def sandbox():
    from metaskill.evaluate.sandbox import DockerSandbox
    box = DockerSandbox()
    problem = box.check()
    if problem:
        pytest.skip(f"Docker sandbox unavailable: {problem}")
    return box


@needs_hermes
def test_hermes_real_tools_reach_the_container_and_nothing_else_can_be_created(sandbox):
    workspace = sandbox.open([])
    try:
        check = HermesAgent().check(workspace)["check"]
        assert '"/work/check.txt"' in check["write_file"]
        assert "/work\\nnobody\\nwritten by the file tool" in check["terminal"]
        assert "from-terminal" in check["read_file"]
        assert all(v.startswith("refused") for v in check["guards"].values()), check["guards"]
        assert "from-terminal" in workspace.run_command("cat check2.txt", 30).stdout
    finally:
        workspace.close()


@needs_hermes
def test_a_model_the_provider_does_not_have_is_an_error_not_a_judged_run(sandbox):
    workspace = sandbox.open([])
    try:
        with pytest.raises(HermesAgentUnavailable, match="provider could not serve"):
            HermesAgent(model="definitely-not-a-real-model-xyz", timeout=180).run("Say hi.", None, workspace)
    finally:
        workspace.close()


@pytest.mark.skipif(not os.environ.get("METASKILL_HERMES_LIVE"),
                    reason="set METASKILL_HERMES_LIVE=1 (real model calls)")
def test_a_real_session_through_the_runner(tmp_path, sandbox):
    skill = make_skill(tmp_path, {"scripts/total.sh": "#!/bin/sh\necho 60\n"})
    (skill.path / "scripts/total.sh").chmod(0o755)
    (tmp_path / "sales.csv").write_text("item,amount\na,10\nb,20\nc,30\n")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"tasks": [{
        "task_id": "sum", "description": "d", "fixtures": ["sales.csv"],
        "prompt": "Write the total of the amount column of sales.csv into total.txt (just the number).",
        "checks": [{"judge": "deterministic", "kind": "exit_code",
                    "target": 'test "$(cat total.txt)" = 60', "expected": "0"}],
    }]}))
    result = run_skill(skill, tasks, HermesAgent(max_iterations=10),
                       config=EvaluationConfig(runs_per_task=1), run_dir=tmp_path / "run",
                       sandbox=sandbox)
    assert not result.errors
    assert [t.variant.value for t in result.tests] == ["with_skill", "baseline"]
    for t in result.tests:
        assert t.metrics.llm_calls > 0 and t.metrics.tool_calls is not None
