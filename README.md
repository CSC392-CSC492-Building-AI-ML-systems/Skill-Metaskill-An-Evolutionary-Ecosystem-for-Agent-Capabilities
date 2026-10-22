# Metaskill

A [Hermes Agent](https://hermes-agent.nousresearch.com/) plugin to generalize, personalize,
evaluate, and merge Hermes skills.

## Install

```bash
ln -s "$PWD" ~/.hermes/plugins/metaskill
hermes plugins enable metaskill
```

### Optional: Laya PII detector

`generalize` can detect personal data with the local Laya Experts model (no
key, no signup). Install the companion plugin, which pulls in the `laya`
package after Hermes asks for consent:

```bash
hermes plugins install metaskill-laya
```

Then opt in per call with `use_laya=true`. The public ``goku-san/laya-experts``
model downloads into ``~/.cache/huggingface`` on first use, then runs entirely
locally. Pass `laya_model_path` to use a local copy of the model instead.

If you opt in and laya is missing, the result tells the agent to ask you first
and then point you to the command above; it never installs anything itself.

The bare-name command needs `metaskill-laya` to be listed in the Hermes plugin
catalog. Until then, Hermes can install this repo by `owner/repo` or Git URL,
but not the `plugins/metaskill-laya` subfolder on its own. Developers working
outside Hermes can use `pip install -r optional_requirements.txt`.

## Layout

| Path | Purpose |
|---|---|
| `plugin.yaml`, `__init__.py` | Plugin manifest and `register(ctx)` |
| `schemas.py`, `tools.py` | Tool schemas and handlers |
| `metaskill/generalize/` | Part 1: PII detection, placeholders, config templates |
| `metaskill/personalize/` | Part 1: rebuild a skill from a template + config |
| `metaskill/evaluate/` | Part 2: scoring and ranking skills |
| `metaskill/merge/` | Part 3 (stretch): skill breeding |
| `plugins/metaskill-laya/` | Optional companion plugin that installs `laya` |
| `skills/` | Skills bundled with the plugin |
| `tests/` | pytest tests and sample skills |
| `examples/` | Example config files |
| `docs/` | Design docs, limitations, meeting notes |
