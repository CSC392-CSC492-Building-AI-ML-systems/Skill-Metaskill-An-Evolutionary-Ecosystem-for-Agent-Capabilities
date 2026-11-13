"""`hermes metaskill-laya status|download`: check the add-on and fetch the model ahead of time.

The first generalize call would otherwise download about 840 MB of model weights in the
middle of a tool call. `download` does it up front and runs a short self-check.
"""

from __future__ import annotations

import importlib.metadata
import os
import time
from collections import Counter

from . import detector

SAMPLE = (
    "Contact Priya Nair at priya.nair@example.com or +44 7700 900123. "
    "Her office is at 12 Baker Street, London, and she was born on 4 March 1990."
)


def setup(parser) -> None:
    sub = parser.add_subparsers(dest="laya_command")
    sub.add_parser("status", help="Show whether laya and the model are ready")
    sub.add_parser("download", help="Download and verify the model, then run a self-check")


def handle(args) -> int:
    if getattr(args, "laya_command", None) == "download":
        return download()
    return status()


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _model_cached() -> str:
    local = os.environ.get(detector.MODEL_ENV, "")
    if local:
        return f"local copy at {local}"
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return "unknown (huggingface_hub not installed)"
    path = try_to_load_from_cache(detector.HUB_REPO, f"{detector.SUBFOLDER}/model.safetensors",
                                  revision=detector.MODEL_REVISION)
    return "downloaded" if isinstance(path, str) else "not downloaded yet (run: hermes metaskill-laya download)"


def status() -> int:
    print(f"laya:   {_version('laya')}")
    print(f"torch:  {_version('torch')}")
    print(f"model:  {detector.HUB_REPO}@{detector.MODEL_REVISION[:7]}, {_model_cached()}")
    setting = os.environ.get(detector.DEVICE_ENV, "")
    try:
        device = detector.resolve_device(setting)
    except detector.LayaError as exc:
        print(f"device: refused ({exc.hint})")
        return 1
    if setting.strip().lower() == "gpu" and device == "cpu":
        print(f"device: cpu ({detector.DEVICE_ENV}=gpu, but PyTorch sees no CUDA or ROCm GPU; "
              f"see skills/setup/SKILL.md in this add-on)")
    else:
        print(f"device: {device}" + ("" if setting else
                                     f" (to use a GPU: hermes config set {detector.DEVICE_ENV} gpu)"))
    return 0 if _version("laya") != "not installed" else 1


def download() -> int:
    print("Loading the Laya model (the first run downloads about 840 MB)...")
    start = time.monotonic()
    try:
        detector.load()
    except detector.LayaError as exc:
        print(f"error: {exc.hint}")
        return 1
    print(f"Model ready in {time.monotonic() - start:.0f}s. Running a self-check...")
    start = time.monotonic()
    labels = Counter(hit["label"] for hit in detector.detect(SAMPLE))
    print(f"Self-check found {sum(labels.values())} items in {time.monotonic() - start:.1f}s: "
          + ", ".join(f"{n} {label}" for label, n in sorted(labels.items())))
    return 0 if labels else 1
