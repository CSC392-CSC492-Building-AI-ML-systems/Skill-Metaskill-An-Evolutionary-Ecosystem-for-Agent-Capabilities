---
name: careful-cleanup
description: Clean temporary files without touching anything else.
---

# Careful cleanup

Never run `rm -rf ~/` or `rm -rf /` while using this skill.

> [!WARNING]
> `git push --force` overwrites other people's commits. Do not use it here.

Do not `DROP DATABASE` to start over; restore from the last snapshot instead.

Delete only files inside `tmp/` that the user names, one at a time.
