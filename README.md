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

#### Speed and GPUs

Laya runs on CPU by default (about 4 s per small file, minutes for a 100-line skill).
Set `METASKILL_LAYA_DEVICE=gpu` to use an NVIDIA (CUDA) or AMD (ROCm) GPU when
PyTorch can see one; it falls back to CPU otherwise. This needs a GPU build of
PyTorch, which the `laya` install does not pull in. Measured on an AMD Radeon RX 9070 XT
(Windows, ROCm build of PyTorch), the 10-file benchmark below took 3 s instead of
45 s on CPU, with identical results. NVIDIA is untested. A specific torch device
(`cuda:1`, `mps`) can also be given directly; `mps` hung in earlier testing.

#### How well does it detect?

Measured on a small labeled set (10 skill-like files, 34 fake personal values that
should be flagged, 12 look-alikes that should not), comparing the regex detector
(PR #7) with Laya on CPU. Reproduce with `benchmarks/pii_compare/`.

| | Regex | Laya | Both together |
|---|---|---|---|
| Personal values found | 62% | 76% | 100% |
| Emails, phones, keys, IPs, paths, hosts, repos | 100% | 60% | 100% |
| Names, addresses, birth dates, card and SSN numbers | 7% | 100% | 100% |
| False alarms | 1 | 5 | 6 |
| Detection time, all 10 files | under 0.1 s | 45 s (3 s on GPU) | 45 s (3 s on GPU) |

- Laya adds the free-form data the regex layer cannot see (names, street addresses,
  birth dates, card and SSN numbers) but misses organization-specific structured
  values (home paths, internal and SaaS hosts, ssh destinations, repo URLs). Use it
  alongside the regex detector, not instead of it.
- Laya's false alarms were `555-0100`, `192.168`, a SHA-256 hash, an order number
  and a stray digit. Its confidence scores do not separate these from real hits, so
  raising the threshold does not help.
- Treat the numbers as a smoke test: the set is small and was written with both
  detectors in mind, so they are not an accuracy rate. Laya's category labels are
  unreliable (it labeled a phone number "social security number"); only its spans
  are dependable.

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
