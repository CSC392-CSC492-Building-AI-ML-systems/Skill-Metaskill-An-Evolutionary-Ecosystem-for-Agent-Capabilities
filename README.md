# Metaskill

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin to generalize, personalize,
evaluate, and merge Hermes skills.

## Install

```bash
ln -s "$PWD" ~/.hermes/plugins/metaskill
hermes plugins enable metaskill
```

`evaluate` also needs [Docker](https://docs.docker.com/get-started/get-docker/), installed and running, to run task checks in a sandbox, and the sandbox image:

```bash
docker pull python:3.12-slim
```

`generalize` does not need Docker. If something is missing, the plugin lists it before evaluating ([docs/evaluate/dependencies.md](docs/evaluate/dependencies.md)).

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
