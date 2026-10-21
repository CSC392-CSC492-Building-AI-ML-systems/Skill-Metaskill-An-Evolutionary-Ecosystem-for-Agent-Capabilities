import sys
from pathlib import Path

# Lets any test import the shared helpers in tests/, e.g. `from hermes_skills import load`.
sys.path.insert(0, str(Path(__file__).parent))

# Fixture folders are test data, not tests: some Hermes skills ship their own test files.
collect_ignore_glob = ["fixtures/*", "*/fixtures/*"]
