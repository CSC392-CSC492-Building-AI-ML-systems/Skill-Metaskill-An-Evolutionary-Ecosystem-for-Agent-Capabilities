"""Rewrite tests/safety/hermes_expected.json and hub_expected.json from the current own
checks.

Run after a rule change that is meant to change findings on real skills:

    python tests/safety/update_hermes_snapshot.py

then review the diff of both files before committing them.
"""

import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path[:0] = [str(here.parents[1]), str(here.parent), str(here)]  # repo root, tests/, tests/safety/

from hermes_snapshot import SETS, results  # noqa: E402

if __name__ == "__main__":
    for skills, snapshot in SETS:
        data = results(skills)
        snapshot.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"wrote {snapshot}: {len(data)} skills, "
              f"{sum(len(v) for v in data.values())} findings")
