"""Small demo: run the Laya PII detector on some text and print what it finds.

    python examples/laya_demo.py                  # built-in sample with fake personal data
    python examples/laya_demo.py path/to/SKILL.md # your own file

The first run downloads the model (about 1 GB) from Hugging Face; after that it
runs locally. This demo prints the detected values so you can see them. The
metaskill tool itself never does that: it only returns counts.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metaskill.generalize import laya_pii  # noqa: E402

SAMPLE = """\
# Deploy helper

Contact Priya Nair at priya.nair@example.com or +44 7700 900123.
Her office is at 12 Baker Street, London, and she was born on 4 March 1990.
Billing card: 4111 1111 1111 1111. Docs: https://docs.example.com/deploy
Run `deploy --region us-east-1` after you read the guide.
"""


def main():
    if len(sys.argv) > 1:
        text = Path(sys.argv[1]).read_text(encoding="utf-8")
        source = sys.argv[1]
    else:
        text, source = SAMPLE, "built-in sample"

    print(f"Input: {source} ({len(text.splitlines())} lines)")
    print("Loading Laya model (first run downloads ~1 GB)...")
    try:
        start = time.time()
        laya_pii.load_detector()
        print(f"Model ready in {time.time() - start:.1f}s")
    except laya_pii.LayaUnavailable as exc:
        sys.exit(f"{exc}\nOr, outside Hermes: pip install -r optional_requirements.txt")

    start = time.time()
    entities = laya_pii.scan(text)
    print(f"Scanned in {time.time() - start:.1f}s: {len(entities)} item(s) found\n")

    print(f"{'label':<24} {'score':<6} text")
    print("-" * 60)
    for e in sorted(entities, key=lambda e: e["start"]):
        print(f"{e['label']:<24} {e['score']:<6} {e['text']!r}")

    print("\nRedacted preview:")
    redacted, next_start = text, len(text)
    for e in sorted(entities, key=lambda e: e["start"], reverse=True):
        if e["end"] > next_start:  # overlaps a span already redacted
            continue
        redacted = redacted[: e["start"]] + f"[{e['label'].upper()}]" + redacted[e["end"]:]
        next_start = e["start"]
    print(redacted)


if __name__ == "__main__":
    main()
