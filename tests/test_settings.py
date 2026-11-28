"""/metaskill settings: evaluate's saved settings (metaskill/evaluate/settings.py,
docs/evaluate/settings.md). conftest.py gives every test its own, empty saved file."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import metaskill.evaluate as evaluate
import tools
from metaskill.evaluate import settings
from metaskill.evaluate.models import EvaluationConfig, SafetyTier
from test_evaluate import load, make_skill
from test_plans import command, root, saved, skills  # noqa: F401  (fixtures)


@pytest.fixture
def saved_file(_own_saved_settings):
    return _own_saved_settings


def handler(**ctx):
    ctx.setdefault("session_cwd", lambda: "")
    ctx.setdefault("inject_message", lambda text: pytest.fail(f"sent to the agent: {text}"))
    return tools.make_command_handler(SimpleNamespace(**ctx))


class Asker:
    """ctx.ask_choice: the given answers in order, then None (a cancel)."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, question, choices):
        self.asked.append((question, choices))
        return self.answers.pop(0) if self.answers else None


# --- The settings and the saved file --------------------------------------------


def test_the_ticket_lists_the_settings_and_leaves_out_weights_and_tasks():
    names = [s.name for s in settings.SETTINGS]
    assert names[:8] == ["runs_per_task", "max_parallel_runs", "prewarm_agents", "safety_tier",
                         "agent_model", "agent_provider", "agent_max_iterations",
                         "agent_timeout"]
    assert all(s.common for s in settings.SETTINGS[:8])
    assert not any(s.common for s in settings.SETTINGS[8:])
    assert "weights" not in names and "tasks_path" not in names
    # Each is a real EvaluationConfig field, with its default from there.
    assert set(settings.defaults()) <= set(EvaluationConfig().to_dict())


def test_show_lists_every_setting_with_its_default_and_the_file(saved_file):
    text = settings.show()
    assert "runs_per_task: 3 (default)" in text and "agent_timeout: 600 s (default)" in text
    assert "agent_model: Hermes' own (default)" in text
    assert text.index("Common:") < text.index("Advanced:") < text.index("sandbox_disk")
    assert f"Saved in {saved_file} (nothing saved yet)" in text


def test_set_validates_saves_and_shows_the_change(saved_file):
    assert settings.set_value("runs_per_task", "5") == ("3", "5")
    assert settings.set_value("prewarm_agents", "off") == ("on", "off")
    assert settings.set_value("agent_timeout", "300s") == ("600 s", "300 s")
    assert json.loads(saved_file.read_text()) == {
        "agent_timeout": 300, "prewarm_agents": False, "runs_per_task": 5}
    assert "runs_per_task: 5 (saved; default 3)" in settings.show()


@pytest.mark.parametrize("name, value, why", [
    ("runs_per_task", "0", "runs_per_task must be >= 1"),
    ("runs_per_task", "five", "whole number"),
    ("max_parallel_runs", "40", "from 1 to 16"),
    ("safety_tier", "loud", "one of static, deep"),
    ("prewarm_agents", "maybe", "on or off"),
    ("sandbox_disk", "lots", "a size like 16m"),
    ("agent_model", "a\x1bb", "printable"),
])
def test_an_invalid_value_is_refused_and_nothing_is_saved(saved_file, name, value, why):
    with pytest.raises(settings.SettingsError, match=why):
        settings.set_value(name, value)
    assert not saved_file.exists()


@pytest.mark.parametrize("name, why", [
    ("run_per_task", "did you mean runs_per_task?"),
    ("weights", "set only in a --config file"),
    ("weights.safety", "set only in a --config file"),
    ("tasks_path", "--tasks <file>"),
])
def test_an_unknown_name_is_refused_with_a_reason(name, why):
    with pytest.raises(settings.SettingsError, match=why.replace("?", r"\?")):
        settings.set_value(name, "1")


def test_reset_one_or_all(saved_file):
    settings.set_value("runs_per_task", "5")
    settings.set_value("safety_tier", "deep")
    assert settings.reset("runs_per_task") == ["runs_per_task"]
    assert settings.reset("runs_per_task") == []  # already the default
    assert json.loads(saved_file.read_text()) == {"safety_tier": "deep"}
    assert settings.reset() == ["safety_tier"] and not saved_file.exists()


def test_a_corrupt_file_is_named_and_reset_repairs_it(saved_file):
    saved_file.parent.mkdir(parents=True, exist_ok=True)
    saved_file.write_text("{not json")
    with pytest.raises(settings.SettingsError, match="cannot be read") as e:
        settings.show()
    assert str(saved_file) in str(e.value)
    settings.reset()
    assert "nothing saved yet" in settings.show()


def test_an_invalid_hand_edited_value_is_named(saved_file):
    saved_file.parent.mkdir(parents=True, exist_ok=True)
    saved_file.write_text(json.dumps({"runs_per_task": 0}))
    with pytest.raises(settings.SettingsError, match="cannot be used: runs_per_task"):
        settings.load_saved()


def test_weights_or_an_unknown_key_in_the_saved_file_are_ignored_with_a_warning(saved_file):
    saved_file.parent.mkdir(parents=True, exist_ok=True)
    saved_file.write_text(json.dumps({"weights": {"safety": 9}, "runz": 1, "runs_per_task": 2}))
    values, warnings = settings.load_saved()
    assert values == {"runs_per_task": 2}
    assert any("weights" in w and "--config" in w for w in warnings)
    assert any("runz" in w for w in warnings)


def test_a_file_that_cannot_be_written_is_a_clear_error(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(settings, "settings_file", lambda: blocker / "evaluate.json")
    with pytest.raises(settings.SettingsError, match="could not be saved"):
        settings.set_value("runs_per_task", "5")


# --- The menu ------------------------------------------------------------------


def test_the_menu_picks_a_setting_then_a_value_saves_it_and_comes_back(saved_file):
    ask = Asker("runs_per_task", "5", settings.EXIT)
    out = settings.menu(ask)
    assert json.loads(saved_file.read_text()) == {"runs_per_task": 5}
    question, choices = ask.asked[0]
    assert [c["value"] for c in choices][-1] == settings.EXIT
    assert choices[0]["label"] == "runs_per_task: 3 (default)"
    # The value menu: the default, the suggested values, a custom value, back.
    values = [c["value"] for c in ask.asked[1][1]]
    assert values == [settings.DEFAULT, "1", "2", "3", "5", "10", settings.CUSTOM, settings.BACK]
    assert "(current)" in ask.asked[1][1][3]["label"]
    # Back at the setting menu, which says what was saved.
    assert ask.asked[2][0].startswith("Saved runs_per_task: 3 → 5.")
    assert ask.asked[2][1][0]["label"] == "runs_per_task: 5 (saved; default 3)"
    assert "runs_per_task: 3 → 5" in out


def test_default_resets_and_back_changes_nothing(saved_file):
    settings.set_value("safety_tier", "deep")
    out = settings.menu(Asker("safety_tier", settings.BACK, "safety_tier", settings.DEFAULT,
                              settings.EXIT))
    assert not json.loads(saved_file.read_text())
    assert "safety_tier: deep → static" in out


def test_a_cancel_or_timeout_ends_the_menu_keeping_what_was_saved(saved_file):
    out = settings.menu(Asker("max_parallel_runs", "4"))  # then None: cancelled
    assert json.loads(saved_file.read_text()) == {"max_parallel_runs": 4}
    assert "max_parallel_runs: 16 → 4" in out


def test_custom_value_ends_with_the_command_to_type(saved_file):
    out = settings.menu(Asker("agent_model", settings.CUSTOM))
    assert "/metaskill settings set agent_model <value>" in out
    assert "No settings changed." in out


def test_choices_without_free_text_offer_no_custom_value():
    ask = Asker("safety_tier")
    settings.menu(ask)
    assert [c["value"] for c in ask.asked[1][1]] == [settings.DEFAULT, "static", "deep",
                                                     settings.BACK]


def test_a_refused_value_is_said_and_the_menu_goes_on(saved_file, monkeypatch):
    def refuse(name, text, path=None):
        raise settings.SettingsError("max_parallel_runs must be an integer from 1 to 16")
    monkeypatch.setattr(settings, "set_value", refuse)
    ask = Asker("max_parallel_runs", "8", settings.EXIT)
    settings.menu(ask)
    assert ask.asked[2][0].startswith("Not saved: max_parallel_runs must be")


def test_no_answer_to_the_first_question_means_nothing_can_ask():
    assert settings.menu(Asker()) is None


# --- The command -----------------------------------------------------------------


def test_the_command_opens_the_menu_where_hermes_can_ask(saved_file):
    out = handler(ask_choice=Asker("runs_per_task", "2", settings.EXIT))("settings")
    assert "runs_per_task: 3 → 2" in out


@pytest.mark.parametrize("ctx", [{}, {"ask_choice": Asker()}])
def test_without_a_prompt_the_command_prints_the_settings_and_the_usage(ctx):
    out = handler(**ctx)("settings")
    assert "runs_per_task: 3 (default)" in out and out.endswith(settings.USAGE)


@pytest.mark.parametrize("request_", ["settings set", "settings set runs_per_task",
                                      "settings show extra", "settings frobnicate",
                                      "settings reset a b"])
def test_an_incomplete_or_unknown_settings_command_gets_the_usage(request_):
    assert handler()(request_) == settings.USAGE


def test_set_show_and_reset_through_the_command(saved_file):
    run = handler()
    assert run("settings set runs_per_task 4").startswith("runs_per_task: 3 → 4.")
    assert run("settings set runs_per_task 4") == "runs_per_task is already 4."
    assert "runs_per_task: 4 (saved; default 3)" in run("settings show")
    assert run("settings set run_per_task 4").startswith("metaskill settings: there is no")
    assert run("settings reset runs_per_task") == "runs_per_task is back to its default."
    assert run("settings reset") == "Nothing was saved: every setting is already its default."


def test_a_model_name_with_spaces_is_one_value(saved_file):
    handler()('settings set agent_model "my model"')
    assert json.loads(saved_file.read_text()) == {"agent_model": "my model"}


def test_the_usage_lists_settings():
    assert "settings [show | set <setting> <value> | reset [<setting>]]" in tools.USAGE
    assert "settings [show" in handler()("")


def test_the_tool_reads_the_settings_but_cannot_change_them(saved_file):
    settings.set_value("runs_per_task", "5")
    reply = json.loads(tools.metaskill({"operation": "settings"}))
    assert reply["success"] and "runs_per_task: 5 (saved" in reply["settings"]
    changed = json.loads(tools.metaskill({"operation": "settings", "skill_path": "set x 1"}))
    assert changed["settings"] == reply["settings"]  # nothing it passes is a change
    assert json.loads(saved_file.read_text()) == {"runs_per_task": 5}


# --- What a run uses -----------------------------------------------------------


def test_a_run_starts_from_the_saved_settings_and_a_config_file_overrides_its_own(tmp_path):
    settings.set_value("runs_per_task", "5")
    settings.set_value("safety_tier", "deep")
    config, sources, warnings = evaluate._config(None, None)
    assert (config.runs_per_task, config.safety_tier.value) == (5, "deep")
    assert sources == {"runs_per_task": "saved", "safety_tier": "saved"}
    file = tmp_path / "c.json"
    file.write_text(json.dumps({"runs_per_task": 2, "run_per_task": 9}))
    config, sources, warnings = evaluate._config(file, None)
    # The file's runs_per_task wins; safety_tier, which it leaves out, is still saved.
    assert (config.runs_per_task, config.safety_tier.value) == (2, "deep")
    assert sources == {"runs_per_task": "--config", "safety_tier": "saved"}
    assert warnings == ["run_per_task in the config file is not a setting and is ignored"]


def test_a_plans_own_settings_ignore_the_saved_ones(tmp_path):
    settings.set_value("safety_tier", "deep")
    file = tmp_path / "config.json"
    file.write_text(json.dumps({"runs_per_task": 2}))
    config, sources, _ = evaluate._config(file, None, settings_mode="plan")
    assert (config.runs_per_task, config.safety_tier.value) == (2, "static")
    assert sources == {"runs_per_task": "plan"}


def test_the_report_records_the_settings_and_their_source(tmp_path):
    settings.set_value("safety_tier", "deep")
    settings.set_value("judge_retries", "2")
    default = load(evaluate.evaluate_skill(make_skill(tmp_path, "a"),
                                           output_path=tmp_path / "one",
                                           config_path=_file(tmp_path, {"judge_retries": 1})))
    assert default.meta.settings == {"safety_tier": {"value": "deep", "source": "saved"}}
    result = evaluate.evaluate_skill(make_skill(tmp_path, "b"), output_path=tmp_path / "two")
    report = load(result)
    assert report.meta.settings == {"judge_retries": {"value": 2, "source": "saved"},
                                    "safety_tier": {"value": "deep", "source": "saved"}}
    # The hash is of the merged settings (output_dir is not hashed).
    assert report.meta.config == EvaluationConfig(
        judge_retries=2, safety_tier=SafetyTier.DEEP, output_dir=Path("x")).hash()
    text = next(Path(p).read_text(encoding="utf-8") for p in result["artifacts"]
                if p.endswith(".md"))
    assert "- Settings not at their default: judge_retries 2 (saved), safety_tier deep (saved)" \
        in text


def test_an_unusable_saved_file_stops_the_run_with_its_name(saved_file, tmp_path):
    saved_file.parent.mkdir(parents=True, exist_ok=True)
    saved_file.write_text("[1]")
    result = evaluate.evaluate_skill(make_skill(tmp_path, "a"), output_path=tmp_path / "o")
    assert result["status"] == "error" and str(saved_file) in result["error"]


def _file(folder, data):
    path = folder / "given.json"
    path.write_text(json.dumps(data))
    return path


# --- Plans: their own settings, or --override -------------------------------------


def _report(folder):
    path = max(Path(folder).rglob("ranked-evaluation.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_plan_runs_with_its_own_settings_unless_overridden(command, skills, tmp_path):
    command("configure skills/adder, skills/summer --name Sums")
    command("approve Sums")
    settings.set_value("judge_retries", "3")  # not the plan's
    out = command("rank Sums")
    assert out.startswith("Plan plan-") and "--override" not in out.splitlines()[0]
    meta = _report(tmp_path)["meta"]
    assert "settings_override" not in meta and "judge_retries" not in meta.get("settings", {})

    out = command("rank Sums --override")
    assert "with your settings instead of the plan's (--override)" in out.splitlines()[0]
    meta = _report(tmp_path)["meta"]
    assert meta["settings_override"] is True
    assert meta["settings"]["judge_retries"] == {"value": 3, "source": "saved"}


def test_override_takes_a_config_file_too(command, skills, tmp_path):
    command("configure skills/adder, skills/summer --name Sums")
    command("approve Sums")
    (tmp_path / "mine.json").write_text(json.dumps({"judge_retries": 2}))
    command("rank Sums --override --config mine.json")
    meta = _report(tmp_path)["meta"]
    assert meta["settings"]["judge_retries"] == {"value": 2, "source": "--config"}


@pytest.mark.parametrize("extra", ["--config c.json", "--tasks t.json", "--override extra"])
def test_a_plan_still_refuses_anything_but_override(command, skills, extra):
    command("configure skills/adder, skills/summer --name Sums")
    command("approve Sums")
    assert "takes nothing else, but --override" in command(f"rank Sums {extra}")
