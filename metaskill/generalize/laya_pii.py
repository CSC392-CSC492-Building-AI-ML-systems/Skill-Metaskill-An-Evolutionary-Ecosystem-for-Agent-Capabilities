"""Personal-data detection with the Laya Experts "pii" model.

The model is downloaded from the Hugging Face Hub on first use (anonymous, no
key, no signup), or loaded from a local directory when ``model_path`` is set.
After that, detection runs locally with no outbound network traffic.

laya itself is supplied by the optional ``metaskill-laya`` companion plugin
(plugins/metaskill-laya), which declares it as a dependency.

There is no fallback detector. If laya is not installed, ``load_detector``
raises ``LayaUnavailable`` so the caller can report it instead of silently
returning fewer results.
"""

from __future__ import annotations

import os
import threading

HUB_REPO = "goku-san/laya-experts"
# CPU by default: on an Apple-silicon Mac laya picks the GPU (MPS) on its own, and
# there it hung indefinitely. Set this to "mps" or "cuda" to opt back in.
DEVICE_ENV = "METASKILL_LAYA_DEVICE"
INSTALL_HINT = "laya is not installed; run: hermes plugins install metaskill-laya"


class LayaUnavailable(RuntimeError):
    """laya is not installed, so no detector can be built."""


_MODELS: dict[str, object] = {}
_MODELS_LOCK = threading.Lock()


def load_detector(model_path: str = ""):
    """Return a detector for *model_path* ("" means the Hub model), cached per path."""
    with _MODELS_LOCK:
        if model_path not in _MODELS:
            try:
                from .pii_demo import PiiRedactor

                # The constructor imports laya itself, so it belongs inside the try.
                _MODELS[model_path] = PiiRedactor(
                    model=model_path or HUB_REPO,
                    device=os.environ.get(DEVICE_ENV, "cpu"),
                )
            except ImportError as exc:
                raise LayaUnavailable(INSTALL_HINT) from exc
        return _MODELS[model_path]


def scan(text: str, model_path: str = "", threshold: float = 0.5, region: str = "US") -> list[dict]:
    """Return personal-data entities found in *text*.

    Each entity is a dict with ``start``, ``end``, ``text``, ``label``, ``score``
    and ``category``. Errors from the model propagate to the caller.
    """
    if not text or not text.strip():
        return []
    det = load_detector(model_path).detect(text, threshold=threshold, region=region)
    return [
        {
            "start": e.start,
            "end": e.end,
            "text": e.text,
            "label": e.label,
            "score": round(float(e.score), 4),
            "category": e.label.lower().replace(" ", "_"),
        }
        for e in det.entities
    ]
