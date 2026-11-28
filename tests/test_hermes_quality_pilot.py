import json
import threading
from types import SimpleNamespace

from metaskill.evaluate.golden.pilot_runner import PILOT_ROOT
import metaskill.evaluate as evaluate
from sandbox_helpers import FakeSandbox
from tools import make_command_handler


def test_quality_pilot_command_reports_paired_completions(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: FakeSandbox())
    gold = (PILOT_ROOT / "expected/normal.json").read_text(encoding="utf-8")
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=gold, provider="test", model="fixed",
                               usage=SimpleNamespace(input_tokens=30, output_tokens=12))

    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=complete))
    output = make_command_handler(ctx)("quality-pilot")
    assert "Text-only Quality pilot" in output
    assert "0.75" in output
    assert len(calls) == 6
    assert all(c["messages"][-1] == calls[0]["messages"][-1] for c in calls)
    # Three with the skill (its body as a second system message), three without, in any
    # order: sessions run in parallel.
    assert sorted(len(c["messages"]) for c in calls) == [2, 2, 2, 3, 3, 3]
    assert all(gold.strip() not in str(c["messages"]) for c in calls)
    [path] = (tmp_path / "evaluation-reports").glob("*/ranked-evaluation.json")
    [skill] = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert skill["scores"]["quality"] == .75
    assert skill["lift"] == 0
    for run in skill["tests"]:
        assert run["metrics"]["input_tokens"] == 30
        assert run["metrics"]["output_tokens"] == 12
        assert run["metrics"]["llm_calls"] == 1


def test_quality_pilot_provider_failure_is_missing_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: FakeSandbox())

    def complete(**kwargs):
        raise RuntimeError("private provider error")

    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=complete))
    output = make_command_handler(ctx)("quality-pilot")
    assert "private provider error" not in output
    [path] = (tmp_path / "evaluation-reports").glob("*/ranked-evaluation.json")
    [skill] = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert skill["scores"]["quality"] is None and skill["lift"] is None
    assert skill["dimensions"]["quality"]["status"] == "error"
    assert not skill["tests"]


def test_quality_pilot_requires_completion_access_and_no_arguments(tmp_path):
    handler = make_command_handler(SimpleNamespace(session_cwd=lambda: str(tmp_path)))
    assert "completion access is unavailable" in handler("quality-pilot")
    assert "bundled synthetic task only" in handler("quality-pilot other-skill")


def test_evaluate_himalaya_with_opt_in_quality(tmp_path, monkeypatch):
    from metaskill.evaluate.golden.hermes_pilot import HIMALAYA
    from metaskill.evaluate import models

    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: FakeSandbox())
    # Hermes supplies the YAML loader at runtime; this test runs outside Hermes.
    monkeypatch.setattr(models, "load_yaml", lambda _: {"name": "himalaya"})
    cases = json.loads((PILOT_ROOT.parent / "himalaya/cases.json").read_text(
        encoding="utf-8"))["cases"]
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        messages = kwargs["messages"]
        case = next(c for c in cases if messages[-1]["content"].startswith(c["prompt"]))
        with_skill = len(messages) == 3
        passes = (case["id"] == "himalaya-auth-failure"
                  or with_skill == (case["id"] == "himalaya-sent-envelopes"))
        return SimpleNamespace(text=json.dumps(case["expected"] if passes else {}),
                               provider="test", model="fixed")

    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=complete))
    handler = make_command_handler(ctx)
    output = handler(f'evaluate "{HIMALAYA}" --quality-pilot')
    assert "Text-only Quality pilot" in output
    assert len(calls) == 18, output
    # Sessions run in parallel, so calls of different tasks can interleave: grouped by
    # their task prompt (the last message), each task has three runs with the skill and
    # three without.
    groups = {}
    for c in calls:
        groups.setdefault(c["messages"][-1]["content"], []).append(c)
    assert len(groups) == 3
    for prompt, group in groups.items():
        assert sorted(len(c["messages"]) for c in group) == [2, 2, 2, 3, 3, 3]
        assert not any(json.dumps(case["expected"]) in prompt for case in cases)
    [path] = (tmp_path / "evaluation-reports").glob("*/ranked-evaluation.json")
    [skill] = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert skill["name"] == "himalaya"
    assert skill["scores"]["quality"] == .5 and skill["lift"] == 0
    report = path.with_suffix(".md").read_text(encoding="utf-8")
    # Per-task presentation belongs to MET-49's shared task table (#52).
    assert "Quality by task" not in report
    assert len(skill["tests"]) == 18


def test_evaluate_pilot_rejects_unreviewed_skill_before_completion(tmp_path):
    skill = tmp_path / "private"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Private test fixture", encoding="utf-8")
    calls = []
    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=lambda **kw: calls.append(kw)))
    assert "supports only the bundled" in make_command_handler(ctx)(
        'evaluate private --quality-pilot')
    assert not calls


def test_pilot_option_uses_argument_parser_in_either_position():
    from tools import _parse_evaluate_command

    expected = (("evaluate", r"C:\skills\my skill", None, None), True)
    assert _parse_evaluate_command(r'evaluate --quality-pilot "C:\skills\my skill"') == expected
    assert _parse_evaluate_command(r'evaluate "C:\skills\my skill" --quality-pilot') == expected
    parsed, pilot = _parse_evaluate_command(
        'evaluate reference --quality-pilot --config "my settings.json"'
    )
    assert parsed == ("evaluate", "reference", "my settings.json", None) and pilot


def test_pilot_route_change_leaves_quality_unscored(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "_make_sandbox", lambda config: FakeSandbox())
    calls, lock = [], threading.Lock()

    def complete(**kwargs):
        # Counted under a lock: sessions run in parallel, and exactly one reply must come
        # from the first model for the change to happen at all.
        with lock:
            calls.append(kwargs)
            first = len(calls) == 1
        return SimpleNamespace(
            text=(PILOT_ROOT / "expected/normal.json").read_text(encoding="utf-8"),
            provider="test", model="first" if first else "changed",
        )

    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=complete))
    make_command_handler(ctx)("quality-pilot")
    [path] = (tmp_path / "evaluation-reports").glob("*/ranked-evaluation.json")
    [skill] = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert skill["scores"]["quality"] is None and skill["lift"] is None
    assert skill["dimensions"]["quality"]["status"] == "error"


def test_pilot_rejects_task_override_before_completions(tmp_path):
    calls = []
    ctx = SimpleNamespace(session_cwd=lambda: str(tmp_path),
                          llm=SimpleNamespace(complete=lambda **kw: calls.append(kw)))
    message = make_command_handler(ctx)(
        f'evaluate "{PILOT_ROOT / "skills/reference"}" --quality-pilot --tasks other.json'
    )
    assert "omit --config and --tasks" in message
    assert not calls


def test_pilot_option_rejects_abbreviations():
    import pytest
    from tools import _parse_evaluate_command

    for option in ("--qual", "--conf"):
        with pytest.raises(ValueError):
            _parse_evaluate_command(f"evaluate reference --quality-pilot {option}")
