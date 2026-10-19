---
name: project-reset
description: Reset a project to a clean state.
---

# Project reset

Clean up by running `rm -rf ~/.cache/project-reset` when finished.

Reset the working tree:

```bash
git reset --hard origin/main
git push --force origin main
```

Remove the build output:

```bash
rm -rf build/
```

Then run `scripts/wipe.sh`.
