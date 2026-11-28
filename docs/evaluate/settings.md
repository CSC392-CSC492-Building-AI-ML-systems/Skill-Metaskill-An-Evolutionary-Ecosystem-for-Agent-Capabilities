# Evaluate settings: `/metaskill settings`

Code: [`metaskill/evaluate/settings.py`](../../metaskill/evaluate/settings.py). Tests: [`tests/test_settings.py`](../../tests/test_settings.py).

The settings `evaluate` and `rank` use, set once and saved, so the user does not write a JSON file for `--config` by hand. `--config <file>` keeps working. Only evaluate's settings are here: `personalize --config` is a skill's own configuration and is unchanged.

## 1. Commands

| Command | Does |
|---|---|
| `/metaskill settings` | The settings menu (section 2) where Hermes can ask; elsewhere, the settings and the usage |
| `/metaskill settings show` | Every setting: its value, its default, and whether it is saved or the default; then the saved file's path |
| `/metaskill settings set <setting> <value>` | Validates and saves one setting, e.g. `set runs_per_task 5`, `set agent_model "my model"` |
| `/metaskill settings reset [<setting>]` | Removes one saved setting, or all of them, so the default applies again |

- An unknown name is refused with the closest one suggested ("there is no setting run_per_task; did you mean runs_per_task?"). `weights` and `tasks_path` are refused with the reason (section 3).
- A value is checked with `EvaluationConfig`'s own rules (ranges, the `sandbox_disk` format) together with the other saved settings before anything is saved. An invalid one is refused with the reason, and nothing is saved.
- Values: whole numbers for counts; seconds as `300` or `300s`; `on`/`off` (or `true`/`false`, `yes`/`no`) for `prewarm_agents`; `static` or `deep`; text of up to 200 printable characters, quoted if it has spaces.
- A settings command that is incomplete or unknown (`settings set runs_per_task`, `settings frobnicate`) prints the usage.

## 2. The menu

`/metaskill settings` asks with Hermes' `ctx.ask_choice` (buttons on Telegram, Discord and Slack; a numbered list elsewhere; the clarify panel in the classic CLI, the TUI and Desktop):

1. **Setting menu.** One entry per setting, `runs_per_task: 5 (saved; default 3)`, common ones first, then **Exit**.
2. **Value menu.** **Default (value)**; suggested values where the range is small, the current one marked; **Custom value** for settings that take more than the suggestions; **Back**.
3. A picked value is validated and **saved at once**, so a menu that is closed or times out loses nothing. The setting menu comes back, saying what was saved ("Saved runs_per_task: 3 → 5.") or why not.
4. **Exit**, a cancel or a timeout ends the menu with the list of what changed.

**Custom value** ends the menu and prints the command to type, `/metaskill settings set agent_model <value>`: `ask_choice` returns only one of its options, so it cannot take a model name or a size. This is open question 1 of the ticket: a free-text prompt (`ctx.ask_text`) would be asked of Hermes upstream.

Where nothing can ask (an older Hermes without `ask_choice`, or one that answers `None` at once: a cron job, a hook, `hermes -z`), the command prints the settings and the usage instead.

## 3. The settings

| Setting | Default | Suggested in the menu |
|---|---|---|
| **Common** | | |
| `runs_per_task` | 3 | 1, 2, 3, 5, 10 |
| `max_parallel_runs` | 16 | 1, 2, 4, 8, 16 |
| `prewarm_agents` | on | on, off |
| `safety_tier` | static | static, deep |
| `agent_model`, `agent_provider` | Hermes' own | — |
| `agent_max_iterations` | 30 | 15, 30, 60 |
| `agent_timeout` | 600 s | 300, 600, 1200 s |
| **Advanced** | | |
| `judge_model`, `judge_provider` | Hermes' own | — |
| `judge_retries` | 1 | 0, 1, 2, 3 |
| `judge_max_chars` | 100000 | — |
| `output_max_chars` | 2000 | — |
| `output_dir` | `evaluation-reports` | — |
| `sandbox_disk` | `16m` | — |

What each one does is in [models.md](models.md), `EvaluationConfig`.

Not settings:

- **`weights`**: the weights of `overall` are a team decision ([evaluate.md](../evaluate.md), open question 3), not a user preference; weights per user would make rankings disagree between users for no visible reason. A `--config` file can still set them, and the report records them in the config hash. In the saved file they are ignored, with a warning.
- **`tasks_path`**: tasks belong to the skills being compared, so they are given per call (`--tasks`).

## 4. The saved file

`metaskill/settings/evaluate.json` under the Hermes home of the current profile (`hermes_constants.get_hermes_home()`), so each profile has its own. It has the format a `--config` file has, holding only the saved settings, so it can be passed with `--config` or shared. It is written through a temporary file and a rename.

A file that cannot be read, or holds an invalid value (edited by hand), stops each evaluate or rank with an error naming the file. `/metaskill settings reset` removes it even then. Keys that are not settings are ignored with a warning.

## 5. Which settings a run uses

Later sources win, setting by setting:

1. The defaults
2. The saved settings
3. `--config <file>`, for the settings it holds: what it leaves out keeps the saved value, or the default. A key in it that `EvaluationConfig` does not read is a warning (a typo such as `run_per_task` was ignored silently before).
4. The command's options (`--tasks`, the output path)

**Plans** ([plans.md](plans.md) 4) run with their own `config.json` and ignore the saved settings, so an approved plan always runs as it was approved. `/metaskill rank <plan> --override` (or `evaluate`) runs it with the user's settings instead, with `--config <file>` too if given; the plan's tasks are used either way. Such a run says so in its output and in the report.

## 6. Reports

`ReportMeta.config`, the config hash, is computed from the merged settings, as before. `ReportMeta.settings` lists each setting that is not its default, with its value and where it came from (`saved`, `--config` or `plan`), and `settings_override` is `true` for a plan run with `--override`. The Markdown report prints them in its header: `- Settings not at their default: runs_per_task 5 (saved), safety_tier deep (--config)`. `output_dir` and `tasks_path` are left out: they say where things are, not how the skills were scored.

## 7. Who can change them

Only the typed command. The model-driven `metaskill` tool can read them (`operation: settings` gives what `settings show` prints) but has no way to change them, as with `approve` for plans: `agent_model`, `max_parallel_runs` and `safety_tier` change what a run costs and does.

## 8. Testing

[`tests/test_settings.py`](../../tests/test_settings.py), with a fake `ask_choice`: the list of settings; `show`, `set` (valid, invalid, unknown names, `weights`, `tasks_path`) and `reset`; a corrupt, invalid or unwritable saved file; the menu (pick, save, back, default, cancel, custom value, a refused value, no answer at all); the command, its fallback and its usage; the tool reading but not changing; the order settings apply in, with a plan's own settings and `--override`; the report's settings, sources and hash. `tests/conftest.py` gives every test its own empty saved file, so no test reads the settings of whoever runs it.
