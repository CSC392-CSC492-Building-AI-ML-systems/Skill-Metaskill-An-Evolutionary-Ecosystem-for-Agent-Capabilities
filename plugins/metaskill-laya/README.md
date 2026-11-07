# metaskill-laya

An optional add-on for the [metaskill](../../README.md) plugin. metaskill's
`generalize` works without it, using its regex layer. With this add-on installed,
`generalize` also runs the local [Laya Experts](https://huggingface.co/goku-san/laya-experts)
PII model, which finds what patterns cannot: names, street addresses, birth dates,
and ID and card numbers. No API key, no signup, and detection never leaves your machine.

## Install

```bash
hermes plugins install CSC392-CSC492-Building-AI-ML-systems/Skill-Metaskill-An-Evolutionary-Ecosystem-for-Agent-Capabilities#plugins/metaskill-laya
hermes metaskill-laya download    # optional: fetch the model now instead of on first use
```

Hermes asks before it installs the `laya` package (with PyTorch). Restart Hermes if it
was running. The model (about 840 MB) is downloaded from Hugging Face once, pinned to
one revision and checked against its SHA-256; after that everything runs offline.

PyTorch is the bulk of the install. On Linux, pip's default PyTorch build includes CUDA
and is several GB; on Windows and macOS it is much smaller.

Turn the add-on off with `hermes plugins disable metaskill-laya`, or remove it with
`hermes plugins remove metaskill-laya`.

## What it does

`generalize` runs the regex layer first, then Laya on each file, skipping the spans
regex already found or rejected as example values. Laya's findings have the category
`personal_data` and the action `review`: they are reported for a decision, never
replaced automatically, because the model's confidence does not separate its false
alarms from real hits. Hits that are impossible whatever the label (a "card number"
with letters in it, a 555 phone number, a one-character postal code) are dropped.

Every file is scored with the region set to `US`, the default in the vendored code.
Non-US postal codes and phone numbers are still proposed, but the model is told the
document is American, and accuracy on non-US skills has not been measured separately.

If Laya cannot run (the package is missing, the download fails), `generalize` still
returns the regex results with a warning that says why.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `METASKILL_LAYA_DEVICE` | `cpu` | `gpu` uses an NVIDIA (CUDA) or AMD (ROCm) GPU when PyTorch sees one, else the CPU. A torch device such as `cuda:1` is used as given, except `mps` (or `mps:0`) and `auto`, which are refused: Laya hung on Apple's GPU, and `auto` lets Laya pick it. |
| `METASKILL_LAYA_MODEL` | unset | A local copy of the model: a checkout of `goku-san/laya-experts` or its `pii/` folder. Used as given, without the revision pin or hash check. |
| `METASKILL_EXTRAS_MAX_SECONDS` | `600` | Time limit for add-on detectors per `generalize` call. Laya checks it between batches of 16 candidates, then stops and says so; the file it was in is not counted and its hits are dropped. The limit does not interrupt the first model download, and it cannot stop a device that has hung. |

Set them with `hermes config set NAME value` (saved to `~/.hermes/.env`), then restart
Hermes. `hermes metaskill-laya status` shows the installed versions, whether the model
is downloaded and which device will be used.

## Using a GPU

| Machine | GPU support |
|---|---|
| Linux, NVIDIA | Works: `hermes config set METASKILL_LAYA_DEVICE gpu`, restart Hermes, and `status` shows `cuda`. PyPI's PyTorch for Linux includes CUDA. |
| Windows, or any AMD GPU | Not inside Hermes. The PyTorch Hermes installs is CPU-only there, and Hermes manages its environment, so a CUDA or ROCm build cannot be swapped in. The CPU works. |
| Mac | Not supported: Laya hung on Apple's GPU (MPS) in testing. |

The full setup and troubleshooting steps are in [skills/setup/SKILL.md](skills/setup/SKILL.md).
The add-on also gives that file to the Hermes agent as the `metaskill-laya:setup` skill,
so you can ask the agent to help with setup.

## Speed and accuracy

On the labeled set in [benchmarks/pii_compare](../../benchmarks/pii_compare)
(10 skill-like files, 34 fake personal values, 12 look-alikes):

| | Regex only | Laya only | Regex + Laya (this add-on) |
|---|---|---|---|
| Personal values found | 62% | 76% | 100% |
| Emails, phones, keys, IPs, paths, hosts, repos | 100% | 60% | 100% |
| Names, addresses, birth dates, card and SSN numbers | 7% | 100% | 100% |
| False alarms | 1 | 2 | 2 (1 from Laya) |
| Time, all 10 files | 0.1 s | 43 s | 34 s on CPU, 2.4 s on GPU |

Measured with laya 0.4.21 on a Windows desktop CPU, and on an AMD Radeon RX 9070 XT
with a ROCm build of PyTorch outside Hermes (identical results). The set is small and was written
with both detectors in mind: treat the numbers as a smoke test, not an accuracy rate.

Laya's category labels are unreliable (it has labeled a card number "driver licence
number"); only its spans are dependable, which is why findings are reported as
`personal_data` and not by Laya's label.

## Development

Outside Hermes: `pip install -r plugins/metaskill-laya/requirements.txt`. Tests:
`pytest tests/laya_addon` (no model needed); `METASKILL_RUN_SLOW=1 pytest tests/laya_addon`
also runs the real model. The range in `plugin.yaml` is the tested one (laya 0.4.17
and 0.4.21); `pii_demo.py` uses laya internals, so rerun the slow tests before
widening it.

`pii_demo.py` is vendored from Laya Experts under the Apache License 2.0; see
[NOTICE](NOTICE) and [LICENSE](LICENSE).
