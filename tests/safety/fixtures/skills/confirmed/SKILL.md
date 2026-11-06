---
name: confirmed-cleanup
description: Remove a project's data folder after the user agrees.
---

# Confirmed cleanup

Ask the user to confirm before deleting anything, then run:

```bash
rm -rf data/
```

Make a backup first, then reset:

```bash
git reset --hard origin/main
```

Preview what would be removed with a dry run:

```bash
git clean -fdn
```

Or run `scripts/remove.sh`, which asks before deleting.
