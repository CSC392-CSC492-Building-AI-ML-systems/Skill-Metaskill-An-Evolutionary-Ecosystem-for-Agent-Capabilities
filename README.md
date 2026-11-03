# Metaskill

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin to generalize, personalize,
evaluate, and merge Hermes skills.

## Install

```bash
ln -s "$PWD" ~/.hermes/plugins/metaskill
hermes plugins enable metaskill
```

### Optional add-on: Laya PII detection

`generalize` finds emails, phone numbers, credentials, paths, hosts and repositories
with its built-in regex layer. The optional **metaskill-laya** add-on also finds what
patterns cannot (names, street addresses, birth dates, ID and card numbers) with a
local model: no API key, nothing leaves your machine, but about 1 GB to install and
slower scans (about 3 seconds per small file on a CPU, much faster on a GPU).

```bash
hermes plugins install CSC392-CSC492-Building-AI-ML-systems/Skill-Metaskill-An-Evolutionary-Ecosystem-for-Agent-Capabilities#plugins/metaskill-laya
hermes metaskill-laya download    # optional: fetch the model now instead of on first use
```

Once installed it runs on every `generalize`; disable it with
`hermes plugins disable metaskill-laya`. When it is not installed, the `generalize`
result tells the agent it exists and to ask you before suggesting it. Settings, speed
and accuracy figures are in [plugins/metaskill-laya](plugins/metaskill-laya/README.md).

## Layout

| Path | Purpose |
|---|---|
| `plugin.yaml`, `__init__.py` | Plugin manifest and `register(ctx)` |
| `schemas.py`, `tools.py` | Tool schemas and handlers |
| `metaskill/generalize/` | Part 1: PII detection, placeholders, config templates |
| `metaskill/personalize/` | Part 1: rebuild a skill from a template + config |
| `metaskill/evaluate/` | Part 2: scoring and ranking skills |
| `metaskill/merge/` | Part 3 (stretch): skill breeding |
| `plugins/metaskill-laya/` | Optional add-on: Laya PII detection for generalize |
| `benchmarks/` | Detector comparisons on labeled samples |
| `skills/` | Skills bundled with the plugin |
| `tests/` | pytest tests and sample skills |
| `examples/` | Example config files and demos |
| `docs/` | Design docs, limitations, meeting notes |
