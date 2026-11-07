---
name: setup
description: Set up and troubleshoot the metaskill-laya add-on - model download, GPU acceleration, and what its generalize warnings mean.
metadata:
  hermes:
    requires_tools: [metaskill]
---

# metaskill-laya setup

The metaskill-laya add-on makes metaskill's `generalize` also find names, street
addresses, birth dates, and ID and card numbers with the local Laya Experts model.
It is installed and enabled. Use this guide when the user asks about setting it up,
making it faster, or a `generalize` warning that mentions laya.

**Rules.** Ask the user before running any command that downloads, installs, or
changes settings, and say what it will do (sizes and restarts included). Never run
pip or uv against Hermes's own Python environment: Hermes manages it, and changes
made by hand can break Hermes or be undone by `hermes pm repair`.

## Check the current state

Run `hermes metaskill-laya status`. It prints the laya and torch versions, whether the
model is downloaded, and the device detection will use (`cpu` or `cuda`).

## Download the model ahead of time

The model is about 840 MB. Without this step, the first `generalize` downloads it.

```
hermes metaskill-laya download
```

This downloads the model once, checks it against its pinned SHA-256, and runs a
self-check. After that, detection works offline.

## Make it faster with a GPU

On a CPU, Laya takes about 3 seconds per small file. A GPU is roughly 10 times faster.
What works depends on the machine. Run `status` first.

- **Linux with an NVIDIA GPU:** supported. PyTorch from PyPI on Linux already
  includes CUDA. Turn it on and restart Hermes:

  ```
  hermes config set METASKILL_LAYA_DEVICE gpu
  ```

  Run `status` again: the device line should say `cuda`.

- **Windows, or any AMD GPU:** the PyTorch that Hermes installs here is CPU-only, and
  Hermes has no supported way to switch it to a CUDA or ROCm build. Tell the user it
  stays on the CPU. Do not try to replace PyTorch in Hermes's environment.

- **Mac:** not supported. Laya hung on Apple's GPU (MPS) in testing, and the time limit
  cannot stop a hung device, so the add-on refuses `mps` and `auto` with a warning.
  Leave the setting at `cpu`.

If `METASKILL_LAYA_DEVICE` is `gpu` but no GPU is usable, the add-on uses the CPU, and
`status` says so.

## Warnings in a generalize result

`generalize` always returns the regex layer's results. When Laya could not finish, a
warning says why:

| Warning | What to do |
|---|---|
| `the <name> package is not importable` | `<name>` is the package that failed to import, such as `laya`, `numpy` or `torch`. If the add-on was just installed, restart Hermes. Otherwise the dependency install was declined or failed: ask the user whether to reinstall the add-on and accept the dependency install. |
| `could not load the Laya model (...)` | Usually the download failed. Check the network, then run `hermes metaskill-laya download` to see the full error. |
| `METASKILL_LAYA_DEVICE=mps is not supported` (or `=auto`) | Ask the user, then set it back with `hermes config set METASKILL_LAYA_DEVICE cpu` and restart Hermes. |
| `laya stopped at the time limit after N of M files` | The skill is large for a CPU, or the first run spent the time downloading the model. Offer a GPU if the machine supports one (see above), or raise the limit: `hermes config set METASKILL_EXTRAS_MAX_SECONDS 1800`. |

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `METASKILL_LAYA_DEVICE` | `cpu` | `gpu` uses an NVIDIA (CUDA) or AMD (ROCm) GPU when PyTorch sees one, else the CPU. `mps` and `auto` are refused. |
| `METASKILL_LAYA_MODEL` | unset | Folder with a local copy of the model, used instead of downloading it. |
| `METASKILL_EXTRAS_MAX_SECONDS` | `600` | Time limit for add-on detection per `generalize` call. |

Set them with `hermes config set NAME value`, which saves them to `~/.hermes/.env`, and
then restart Hermes.

## Turning it off

`hermes plugins disable metaskill-laya` stops it; `generalize` then uses the regex layer
alone. `hermes plugins remove metaskill-laya` uninstalls it.
