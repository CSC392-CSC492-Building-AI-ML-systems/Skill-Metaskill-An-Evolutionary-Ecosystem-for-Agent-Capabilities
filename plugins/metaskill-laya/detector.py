"""Personal-data detection with the Laya Experts "pii" model, for metaskill's generalize.

The model is downloaded from the Hugging Face Hub on first use (anonymous, no key, no
signup), pinned to one revision and checked against its SHA-256 before it is loaded.
After that it runs locally with no outbound traffic for detection.

Importing this module is cheap: laya, torch and the model load on the first detect().

Settings (environment variables):
    METASKILL_LAYA_DEVICE  "cpu" (default), "gpu" (CUDA or ROCm when PyTorch sees one,
                           else CPU), or a torch device such as "cuda:1" or "mps".
    METASKILL_LAYA_MODEL   a local copy of the model: either a checkout of
                           goku-san/laya-experts (with its pii/ folder) or the pii/ folder
                           itself. Used as given, without the revision pin or hash check.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

HUB_REPO = "goku-san/laya-experts"
SUBFOLDER = "pii"
# Bump both together, after checking the new revision against the benchmark.
MODEL_REVISION = "73d5a0883e7000ffac3bdf7bd7de83c5f484e7f4"
MODEL_SHA256 = {"model.safetensors": "3713cff56ada8d6b20bb3b1a60629b39d06a1d6607cc80d5fe6305e1c723322c"}

DEVICE_ENV = "METASKILL_LAYA_DEVICE"
MODEL_ENV = "METASKILL_LAYA_MODEL"
THRESHOLD = 0.5

INSTALL_HINT = (
    "the laya package is not importable. If metaskill-laya was just installed, restart "
    "Hermes; otherwise reinstall the add-on and accept its dependency install"
)


class LayaError(RuntimeError):
    """A known failure. metaskill passes ``hint`` on to the agent."""

    def __init__(self, hint: str):
        super().__init__(hint)
        self.hint = hint


def resolve_device(setting: str = "") -> str:
    """Turn the METASKILL_LAYA_DEVICE setting into a torch device string.

    "" or "cpu" -> "cpu". "gpu" -> "cuda" when PyTorch sees an NVIDIA or AMD (ROCm) GPU,
    else "cpu". Anything else is passed to torch as given. Never picks MPS on its own:
    on an Apple-silicon Mac laya chose MPS and hung.
    """
    setting = setting.strip().lower()
    if setting != "gpu":
        return setting or "cpu"
    try:
        import torch  # imported lazily: laya may not be installed
    except ImportError:
        return "cpu"
    # ROCm builds of PyTorch report AMD GPUs through the "cuda" device name.
    return "cuda" if torch.cuda.is_available() else "cpu"


def model_source(local: str = "") -> dict:
    """PiiRedactor arguments for the Hub model, or for a local copy at *local*."""
    if not local:
        return {"model": HUB_REPO, "subfolder": SUBFOLDER, "revision": MODEL_REVISION,
                "expected_sha256": MODEL_SHA256}
    path = Path(local).expanduser()
    if not path.is_dir():
        raise LayaError(f"{MODEL_ENV} is set, but {path} is not a directory")
    if (path / SUBFOLDER).is_dir():
        return {"model": str(path), "subfolder": SUBFOLDER}
    return {"model": str(path), "subfolder": None}


_MODELS: dict[tuple, object] = {}
_LOCK = threading.Lock()


def load():
    """The PiiRedactor for the current settings, loaded once and cached."""
    source = model_source(os.environ.get(MODEL_ENV, ""))
    device = resolve_device(os.environ.get(DEVICE_ENV, ""))
    key = (source["model"], source["subfolder"], device)
    with _LOCK:
        if key not in _MODELS:
            try:
                from .pii_demo import PiiRedactor  # imports numpy and torch
                _MODELS[key] = PiiRedactor(device=device, **source)
            except ImportError as exc:
                raise LayaError(INSTALL_HINT) from exc
            except (OSError, ValueError, RuntimeError) as exc:
                # Download, file and hash errors name model files, never skill content.
                raise LayaError(f"could not load the Laya model ({type(exc).__name__}: "
                                f"{str(exc)[:200]})") from exc
        return _MODELS[key]


_NUMBER_LABELS = frozenset({
    "phone number", "credit card number", "social security number", "tax number",
    "id card number", "passport number", "driver licence number",
})
_DIGITS = re.compile(r"\D")


def plausible(value: str, label: str) -> bool:
    """False for hits that cannot be what the model says they are.

    Laya scores false alarms as confidently as real hits, so its score cannot filter them.
    These checks only drop values that are impossible whatever the label: the model's
    labels are unreliable (it has called a phone number a social security number), so a
    check that trusted the label, such as a card-number length, could drop a real value.
    """
    if label == "credit card number" and re.search(r"[A-Za-z]", value):
        return False  # a hash or token, not a card
    if label == "postal code" and len(re.sub(r"\W", "", value)) < 3:
        return False  # no postal code is shorter than three characters
    digits = _DIGITS.sub("", value)
    if label in _NUMBER_LABELS and len(digits) in (7, 10, 11):
        national = digits[-7:] if len(digits) == 7 else digits[-10:]
        exchange = national[:3] if len(digits) == 7 else national[3:6]
        if exchange == "555":  # reserved for examples, as in metaskill's regex layer
            return False
    return True


def detect(text: str, skip=()) -> list[dict]:
    """Personal data in *text*, as dicts with start, end, label and score.

    Candidates overlapping a span in *skip* (sorted, non-overlapping) are not scored.
    """
    if not text or not text.strip():
        return []
    found = load().detect(text, threshold=THRESHOLD, skip=list(skip))
    return [
        {"start": e.start, "end": e.end, "label": e.label, "score": round(float(e.score), 4)}
        for e in found.entities
        if plausible(text[e.start:e.end], e.label)
    ]
