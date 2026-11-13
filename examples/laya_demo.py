"""Small demo: run the metaskill-laya add-on's detector on some text and print what it finds.

    python examples/laya_demo.py                  # built-in sample with fake personal data
    python examples/laya_demo.py path/to/SKILL.md # your own file

Needs laya (pip install -r plugins/metaskill-laya/requirements.txt). The first run
downloads the model (about 840 MB) from Hugging Face; after that it runs locally.
This demo prints the detected values so you can see them. The metaskill tool itself
never does that: it only returns counts.
"""

import importlib.util
import sys
import time
import types
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "plugins" / "metaskill-laya"

SAMPLE = """\
# Deploy helper

Contact Priya Nair at priya.nair@example.com or +44 7700 900123.
Her office is at 12 Baker Street, London, and she was born on 4 March 1990.
Billing card: 4111 1111 1111 1111. Docs: https://docs.example.com/deploy
Run `deploy --region us-east-1` after you read the guide.
"""


def load_detector():
    """Import the add-on the way Hermes does, so its relative imports work."""
    sys.modules.setdefault("hermes_plugins", types.ModuleType("hermes_plugins")).__path__ = []
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.metaskill_laya", ADDON / "__init__.py", submodule_search_locations=[str(ADDON)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    return addon.detector


def main():
    if len(sys.argv) > 1:
        text, source = Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[1]
    else:
        text, source = SAMPLE, "built-in sample"
    detector = load_detector()

    print(f"Input: {source} ({len(text.splitlines())} lines)")
    print("Loading the Laya model (the first run downloads about 840 MB)...")
    try:
        start = time.time()
        detector.load()
        print(f"Model ready in {time.time() - start:.1f}s")
    except detector.LayaError as exc:
        sys.exit(f"{exc.hint}\nOutside Hermes: pip install -r plugins/metaskill-laya/requirements.txt")

    start = time.time()
    hits = detector.detect(text)
    print(f"Scanned in {time.time() - start:.1f}s: {len(hits)} item(s) found\n")

    print(f"{'label':<24} {'score':<6} text")
    print("-" * 60)
    for hit in hits:
        print(f"{hit['label']:<24} {hit['score']:<6} {text[hit['start']:hit['end']]!r}")

    print("\nRedacted preview:")
    redacted = text
    for hit in reversed(hits):  # hits are sorted and never overlap
        redacted = redacted[:hit["start"]] + f"[{hit['label'].upper()}]" + redacted[hit["end"]:]
    print(redacted)


if __name__ == "__main__":
    main()
