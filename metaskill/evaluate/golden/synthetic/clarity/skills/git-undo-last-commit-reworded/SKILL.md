---
name: git-undo-last-commit-reworded
description: Take back the latest Git commit but keep its changes, provided it has not been pushed yet.
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

Reverses the most recent commit and keeps all of the work in it. The commit's changes return to the staging area, ready to be corrected and committed again.

## When to Use

- The user wants to redo a commit that was made too soon, on the wrong branch, or with a poor message.

## When Not to Use

- The commit is already pushed. Undoing it would change history that others may already have. Recommend `git revert` instead and explain how the two differ.
- The user wants to discard the changes altogether. That is another, destructive operation; check with the user and do not use this skill for it.

## Steps

1. Make sure the last commit is not pushed yet:

   ```bash
   git status -sb
   ```

   A first line showing `[ahead 1]` (or a higher number) means the commit exists only locally. If nothing is ahead, or the branch has no upstream, check with the user before continuing.

2. Show the user which commit is about to be undone and wait for them to agree:

   ```bash
   git log -1 --oneline
   ```

3. Undo the commit while keeping its changes staged:

   ```bash
   git reset --soft HEAD~1
   ```

4. Check that it worked. `git log -1 --oneline` should now list the commit that came *before* the undone one, and `git status` should show the changes as staged.

## Troubleshooting

- **`HEAD~1` is not recognised:** the repository contains just one commit, so there is nothing to go back to. Tell the user and stop.
- **The wrong commit was undone:** use `git reflog` to find the line for the original commit, then bring it back with `git reset --soft <that-id>`.
