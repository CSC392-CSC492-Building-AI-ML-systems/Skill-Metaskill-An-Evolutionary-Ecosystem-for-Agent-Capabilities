"""Rewrite tests/safety/hermes_expected.json from the current own checks.

Run after a rule change that is meant to change findings on real skills:

    python tests/safety/update_hermes_snapshot.py

then review the diff of hermes_expected.json before committing it.
"""

import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path[:0] = [str(here.parents[1]), str(here.parent), str(here)]  # repo root, tests/, tests/safety/

from hermes_snapshot import SNAPSHOT, results  # noqa: E402

if __name__ == "__main__":
    data = results()
    SNAPSHOT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT}: {len(data)} skills, {sum(len(v) for v in data.values())} findings")
