# Metaskill

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin to generalize, personalize,
evaluate, and merge Hermes skills.

## Install

Link this folder into the `plugins` folder of your Hermes home, then enable it. The home is `~/.hermes` on macOS and Linux, and `%LOCALAPPDATA%\hermes` on Windows.

```bash
ln -s "$PWD" ~/.hermes/plugins/metaskill
hermes plugins enable metaskill
```

```powershell
New-Item -ItemType Junction -Path "$env:LOCALAPPDATA\hermes\plugins\metaskill" -Target "$PWD"
hermes plugins enable metaskill
```

Enabling asks to install Cisco's skill scanner, which `plugin.yaml` declares. Run it in a terminal, and restart Hermes afterwards. Use this command rather than `pip install`, which would reach a different Python.

Once `evaluate` runs test tasks, it also needs [Docker](https://docs.docker.com/get-started/get-docker/), installed and running, to run the agent's actions and task checks in a sandbox, and the sandbox image, pinned by digest:

```bash
docker pull python:3.12-slim@sha256:e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6
```

Running tasks also needs Python 3.12.11, 3.13.4 or later. `generalize` does not need Docker. If something is missing, the plugin lists it ([docs/evaluate/dependencies.md](docs/evaluate/dependencies.md)).

## Usage

```text
/metaskill generalize <skill>
/metaskill personalize <skill> --config <file>
/metaskill evaluate <skill>
/metaskill rank <skill>, <skill>
```

`<skill>` is an installed skill's name (`claude-code`), its category and name (`autonomous-ai-agents/claude-code`), or a path to the skill's folder, relative to your session's folder or absolute. A path that exists wins over a name. If a name matches several skills, the command lists them and runs nothing, so you can give the one you mean. `hermes skills list` shows what is installed.

The command runs these itself and prints the result when it finishes. Anything else after `/metaskill` goes to the agent, which Hermes only allows once you grant it in `config.yaml`. A classic CLI session (`hermes chat`) does not show the agent's reply.

```yaml
plugins:
  entries:
    metaskill:
      allow_gateway_injection: true
```

`evaluate` and `rank` print a table and write a report to `evaluation-reports/[timestamp]/`. Only the Safety dimension is built so far. The others are left empty, so every skill is listed as unranked and none is recommended ([docs/evaluate.md](docs/evaluate.md), section 7).

## Layout

| Path | Purpose |
|---|---|
| `plugin.yaml`, `__init__.py` | Plugin manifest and `register(ctx)` |
| `schemas.py`, `tools.py` | Tool schemas and handlers |
| `metaskill/generalize/` | Part 1: PII detection, placeholders, config templates |
| `metaskill/personalize/` | Part 1: rebuild a skill from a template + config |
| `metaskill/evaluate/` | Part 2: scoring and ranking skills |
| `metaskill/merge/` | Part 3 (stretch): skill breeding |
| `skills/` | Skills bundled with the plugin |
| `tests/` | pytest tests and sample skills |
| `examples/` | Example config files |
| `docs/` | Design docs, limitations, meeting notes |
