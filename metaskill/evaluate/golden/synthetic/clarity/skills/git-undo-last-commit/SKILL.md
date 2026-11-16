---
name: git-undo-last-commit
description: Undo the most recent Git commit while keeping the changes, as long as it has not been pushed.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [Git, version control]
prerequisites:
  commands: [git]
---

# Undo the Last Git Commit

Takes back the last commit without losing any work. The changes from that commit go back to being staged, ready to be fixed and committed again.

## When to Use

- The user committed too early, to the wrong branch, or with a bad message, and wants to redo it.

## When NOT to Use

- The commit has already been pushed. Undoing it would rewrite history other people may have. Suggest `git revert` instead and explain the difference.
- The user wants to throw the changes away. That is a different, destructive operation; confirm with the user and do not use this skill.

## Steps

1. Check that the last commit has not been pushed:

   ```bash
   git status -sb
   ```

   If the first line shows `[ahead 1]` (or more), the commit is local only. If it shows nothing ahead, or the branch has no upstream, ask the user before going further.

2. Show the user the commit that will be undone and get a yes:

   ```bash
   git log -1 --oneline
   ```

3. Undo it, keeping the changes staged:

   ```bash
   git reset --soft HEAD~1
   ```

4. Confirm it worked. `git log -1 --oneline` should now show the commit *before* the one undone, and `git status` should list the changes as staged.

## If Something Goes Wrong

- **`HEAD~1` is unknown:** the repository has only one commit. There is nothing earlier to go back to; tell the user and stop.
- **The wrong commit was undone:** run `git reflog`, find the line for the original commit, and restore it with `git reset --soft <that-id>`.
