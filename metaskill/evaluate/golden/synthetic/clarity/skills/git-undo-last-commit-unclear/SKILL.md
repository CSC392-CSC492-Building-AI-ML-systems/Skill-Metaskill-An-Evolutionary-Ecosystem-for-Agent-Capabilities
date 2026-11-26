---
name: git-undo-last-commit-unclear
description: Soft-reset HEAD to its first parent, preserving the index.
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

Moves the branch ref to `HEAD^` via a soft reset so the tree of the orphaned commit remains staged in the index while the working tree is untouched. Equivalent to `git update-ref` on the symbolic ref plus no index refresh.

## Procedure

1. Verify ref divergence against the tracking ref; non-FF state on the remote implies the object is reachable from `refs/remotes/*` and a history rewrite will require a force-push with lease.
2. `git reset --soft HEAD~1`
3. The orphaned object stays reachable through the reflog until GC prunes it per `gc.reflogExpireUnreachable`.

For merge commits, `HEAD~1` resolves the first parent only; use `HEAD^2` semantics if the second-parent lineage is intended. Detached HEAD state behaves identically modulo the absent symbolic ref.

Recovery: `git reset --soft HEAD@{1}`.
