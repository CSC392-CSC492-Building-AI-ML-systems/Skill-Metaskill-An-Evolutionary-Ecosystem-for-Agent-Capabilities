"""The user's saved evaluate settings: `/metaskill settings` (docs/evaluate/settings.md).

The user sets them once, from a menu or with one-line commands, instead of writing a JSON
file for `--config`. They are saved per Hermes profile in
`metaskill/settings/evaluate.json` under the Hermes home, in the same format `--config`
reads, and every evaluate or rank call starts from them:

    defaults  <  saved settings  <  --config <file>  <  command options

A `--config` file overrides only the settings it holds. An approved plan runs with its own
config.json and ignores the saved settings, unless it is run with `--override`.

`weights` is not a setting: the weights of `overall` are a team decision (evaluate.md,
open question 3), so they are set only in a `--config` file. `tasks_path` is not one
either: tasks belong to the skills being compared, so they are given per call.
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import MAX_PARALLEL_RUNS, EvaluationConfig

USAGE = ("Usage: /metaskill settings [show | set <setting> <value> | reset [<setting>]]\n"
         "  /metaskill settings            the settings menu\n"
         "  /metaskill settings show       every setting, its value and its default\n"
         "  /metaskill settings set <setting> <value>\n"
         "  /metaskill settings reset [<setting>]   one setting, or all, back to the default")
MAX_TEXT_CHARS = 200  # of a text setting such as a model name or a folder


class SettingsError(ValueError):
    """Why a settings command could not be done, or the saved settings cannot be used."""


@dataclass(frozen=True)
class Setting:
    name: str
    kind: str  # "int", "seconds", "bool", "choice", "text" or "size"
    description: str
    common: bool = False
    suggestions: tuple[Any, ...] = ()  # offered in the menu; also the only choices for "choice"


SETTINGS: tuple[Setting, ...] = (
    Setting("runs_per_task", "int", "times each task runs per variant", True,
            (1, 2, 3, 5, 10)),
    Setting("max_parallel_runs", "int", f"task sessions at once, 1 to {MAX_PARALLEL_RUNS}",
            True, (1, 2, 4, 8, 16)),
    Setting("prewarm_agents", "bool", "start Hermes ahead of each session", True,
            (True, False)),
    Setting("safety_tier", "choice", "static, or deep with an LLM review", True,
            ("static", "deep")),
    Setting("agent_model", "text", "model the task sessions use", True),
    Setting("agent_provider", "text", "provider the task sessions use", True),
    Setting("agent_max_iterations", "int", "model turns per task session", True,
            (15, 30, 60)),
    Setting("agent_timeout", "seconds", "seconds per task session", True, (300, 600, 1200)),
    Setting("judge_model", "text", "model of the LLM judge"),
    Setting("judge_provider", "text", "provider of the LLM judge"),
    Setting("judge_retries", "int", "times an unusable judge reply is asked again", False,
            (0, 1, 2, 3)),
    Setting("judge_max_chars", "int", "characters of a skill the judge is sent"),
    Setting("output_max_chars", "int", "characters of a run's output kept in the report"),
    Setting("output_dir", "text", "folder the reports go in, from the session's folder"),
    Setting("sandbox_disk", "size", "size of each run's folder, e.g. 16m"),
)
BY_NAME = {s.name: s for s in SETTINGS}
NOT_SETTINGS = {
    "weights": ("the weights of overall are a team decision (docs/evaluate.md, open question "
                "3), so they are set only in a --config file"),
    "tasks_path": "tasks are given per call, with --tasks <file>",
}
# Every key a --config file may hold; others are reported, as a typo would otherwise be
# silently ignored (EvaluationConfig.from_dict reads only these).
CONFIG_KEYS = frozenset(EvaluationConfig().to_dict())


# --- The saved file ------------------------------------------------------------


def settings_file() -> Path:
    """metaskill/settings/evaluate.json in the Hermes home of the current profile."""
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:  # not inside Hermes
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "metaskill" / "settings" / "evaluate.json"


def load_saved(path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """The saved settings, and warnings for what in the file is ignored. Raises
    SettingsError, naming the file, if it cannot be read or holds an invalid value."""
    path = path or settings_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as e:
        raise SettingsError(f"the saved settings in {path} cannot be read "
                            f"({type(e).__name__}); fix the file, or remove it with "
                            "/metaskill settings reset") from None
    if not isinstance(data, dict):
        raise SettingsError(f"the saved settings in {path} are not a JSON object; fix the "
                            "file, or remove it with /metaskill settings reset")
    values, warnings = {}, []
    for key, value in data.items():
        if key in BY_NAME:
            values[key] = value
        elif key in NOT_SETTINGS:
            warnings.append(f"{key} in {path} is ignored: {NOT_SETTINGS[key]}")
        else:
            warnings.append(f"{_shown_key(key)} in {path} is not a setting and is ignored")
    try:
        _check(values)
    except SettingsError as e:
        raise SettingsError(f"the saved settings in {path} cannot be used: {e}; change it "
                            "with /metaskill settings, or reset it") from None
    return values, warnings


def _save(values: dict[str, Any], path: Path) -> None:
    """Write the settings atomically: a crash leaves the old file or the new one."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(dict(sorted(values.items())), indent=2) + "\n",
                             encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as e:
        raise SettingsError(f"the settings could not be saved in {path} "
                            f"({type(e).__name__})") from None


# --- Values --------------------------------------------------------------------


def defaults() -> dict[str, Any]:
    config = EvaluationConfig().to_dict()
    return {s.name: config[s.name] for s in SETTINGS}


def setting(name: str) -> Setting:
    """The setting called `name`, or SettingsError saying why there is none."""
    if name in BY_NAME:
        return BY_NAME[name]
    if name in NOT_SETTINGS:
        raise SettingsError(f"{name} is not a setting: {NOT_SETTINGS[name]}")
    if name.split(".", 1)[0] == "weights":
        raise SettingsError(f"weights is not a setting: {NOT_SETTINGS['weights']}")
    close = difflib.get_close_matches(name, list(BY_NAME), n=1)
    hint = f"; did you mean {close[0]}?" if close else "; /metaskill settings show lists them"
    raise SettingsError(f"there is no setting {_shown_key(name)}{hint}")


def parse(item: Setting, text: str) -> Any:
    """The value `text` gives `item`, as saved, or SettingsError."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    if item.kind == "int":
        try:
            return int(text)
        except ValueError:
            raise SettingsError(f"{item.name} must be a whole number") from None
    if item.kind == "seconds":
        try:
            value = float(text.removesuffix("s").strip())
        except ValueError:
            raise SettingsError(f"{item.name} must be a number of seconds") from None
        return int(value) if value.is_integer() else value
    if item.kind == "bool":
        word = text.lower()
        if word in ("on", "true", "yes", "1"):
            return True
        if word in ("off", "false", "no", "0"):
            return False
        raise SettingsError(f"{item.name} must be on or off")
    if item.kind == "choice":
        if text.lower() not in item.suggestions:
            raise SettingsError(f"{item.name} must be one of {', '.join(item.suggestions)}")
        return text.lower()
    if not text or len(text) > MAX_TEXT_CHARS or not text.isprintable():
        raise SettingsError(f"{item.name} must be text of 1 to {MAX_TEXT_CHARS} printable "
                            "characters")
    return text


def _check(values: dict[str, Any]) -> None:
    """SettingsError if these settings do not make a valid EvaluationConfig."""
    try:
        EvaluationConfig.from_dict(values)
    except (ValueError, TypeError) as e:
        raise SettingsError(str(e)) from None


def shown(item: Setting, value: Any) -> str:
    """A value as the user reads it."""
    if item.kind == "bool":
        return "on" if value else "off"
    if item.kind == "seconds":
        return f"{float(value):g} s"
    if value == "":
        return "Hermes' own" if item.name.endswith(("_model", "_provider")) else "(empty)"
    return str(value)


def _shown_key(key: object) -> str:
    """A key from a file or a command, safe to print: it may be anything."""
    text = str(key)
    return repr(text[:40]) if not text.isprintable() or len(text) > 40 else text


# --- Commands ------------------------------------------------------------------


def set_value(name: str, text: str, path: Path | None = None) -> tuple[str, str]:
    """Validate and save one setting; (old value, new value) as shown."""
    path = path or settings_file()
    item = setting(name)
    value = parse(item, text)
    saved, _ = load_saved(path)
    old = saved.get(item.name, defaults()[item.name])
    _check({**saved, item.name: value})
    saved[item.name] = value
    _save(saved, path)
    return shown(item, old), shown(item, value)


def reset(name: str | None = None, path: Path | None = None) -> list[str]:
    """Remove one saved setting, or all of them; the names reset. Resetting all works even
    when the saved file cannot be read, as that is how it is repaired."""
    path = path or settings_file()
    if name is None:
        try:
            saved = list(load_saved(path)[0])
        except SettingsError:
            saved = ["(an unreadable file)"]
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            raise SettingsError(f"the settings in {path} could not be removed "
                                f"({type(e).__name__})") from None
        return saved
    item = setting(name)
    saved, _ = load_saved(path)
    if item.name not in saved:
        return []
    del saved[item.name]
    _save(saved, path)
    return [item.name]


def show(path: Path | None = None) -> str:
    """Every setting: its value, whether it is saved, and its default."""
    path = path or settings_file()
    saved, warnings = load_saved(path)
    base = defaults()
    lines = ["Evaluate settings (common first):"]
    for group, title in ((True, "Common"), (False, "Advanced")):
        lines.append(f"{title}:")
        for item in (s for s in SETTINGS if s.common is group):
            lines.append(f"  {_line(item, saved, base)}  ({item.description})")
    lines += [f"Warning: {w}" for w in warnings]
    lines.append(f"Saved in {path}" if path.exists() else f"Saved in {path} (nothing saved yet)")
    return "\n".join(lines)


def _line(item: Setting, saved: dict[str, Any], base: dict[str, Any]) -> str:
    if item.name in saved:
        return (f"{item.name}: {shown(item, saved[item.name])} "
                f"(saved; default {shown(item, base[item.name])})")
    return f"{item.name}: {shown(item, base[item.name])} (default)"


# --- The menu ------------------------------------------------------------------

EXIT, BACK, DEFAULT, CUSTOM = "__exit__", "__back__", "__default__", "__custom__"
Ask = Callable[[str, list], Any]


def menu(ask: Ask, path: Path | None = None) -> str | None:
    """The settings menu: pick a setting, then its new value, which is saved at once; then
    the setting menu again, until Exit, a cancel or a timeout. Returns what to print, or
    None if the very first question got no answer (nothing can ask here): the caller then
    prints the settings and the usage instead."""
    path = path or settings_file()
    changes: list[str] = []
    note, first = "", True
    while True:
        saved, _ = load_saved(path)
        base = defaults()
        choices = [{"value": s.name, "label": _line(s, saved, base), "description": s.description}
                   for s in SETTINGS]
        choices.append({"value": EXIT, "label": "Exit"})
        picked = ask(f"{note}Evaluate settings: pick one to change, or Exit.".strip(), choices)
        if picked is None and first:
            return None
        first = False
        if picked in (None, EXIT):
            break
        item = BY_NAME[picked]
        current = saved.get(item.name, base[item.name])
        options = [{"value": DEFAULT, "label": f"Default ({shown(item, base[item.name])})"}]
        options += [{"value": str(v), "label": shown(item, v) + (" (current)" if v == current
                                                                   else "")}
                    for v in item.suggestions]
        if item.kind != "choice" and item.kind != "bool":
            options.append({"value": CUSTOM, "label": "Custom value"})
        options.append({"value": BACK, "label": "Back"})
        answer = ask(f"{item.name}: {shown(item, current)}. Pick its new value.", options)
        if answer is None:  # cancelled or timed out
            break
        if answer == BACK:
            note = ""
            continue
        if answer == CUSTOM:
            changes.append(f"For a custom {item.name}, type: /metaskill settings set "
                           f"{item.name} <value>")
            break
        old = shown(item, current)
        try:
            if answer == DEFAULT:
                reset(item.name, path)
                new = shown(item, base[item.name])
            else:
                old, new = set_value(item.name, str(answer), path)
        except SettingsError as e:
            note = f"Not saved: {e}. "
            continue
        if old != new:
            changes.append(f"{item.name}: {old} → {new}")
        note = f"Saved {item.name}: {old} → {new}. "
    edits = [c for c in changes if not c.startswith("For a custom")]
    lines = (["Settings changed:", *(f"  {c}" for c in edits), f"Saved in {path}"]
             if edits else ["No settings changed."])
    lines += [c for c in changes if c.startswith("For a custom")]
    return "\n".join(lines)


# --- What a run uses -----------------------------------------------------------


def effective(file_data: Any = None, *, use_saved: bool = True, file_source: str = "--config",
              path: Path | None = None) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """(the settings for EvaluationConfig.from_dict, where each one came from, warnings).
    The saved settings (unless use_saved is False), then a config file's, setting by
    setting. A config file's keys that EvaluationConfig does not read are warned about."""
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    warnings: list[str] = []
    if use_saved:
        saved, warnings = load_saved(path)
        merged.update(saved)
        sources.update(dict.fromkeys(saved, "saved"))
    if file_data is not None:
        if not isinstance(file_data, dict):
            raise ValueError("a config file is a JSON object")
        for key, value in file_data.items():
            if key not in CONFIG_KEYS:
                warnings.append(f"{_shown_key(key)} in the config file is not a setting and "
                                "is ignored")
                continue
            merged[key] = value
            sources[key] = file_source
    return merged, sources, warnings


def differences(config: EvaluationConfig, sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """{setting: {value, source}} for each setting that is not its default, for the report.
    tasks_path and output_dir are left out: they say where things are, not how scored."""
    now, base = config.to_dict(), EvaluationConfig().to_dict()
    return {key: {"value": now[key], "source": sources[key]}
            for key in sorted(sources)
            if key not in ("tasks_path", "output_dir") and now.get(key) != base.get(key)}
