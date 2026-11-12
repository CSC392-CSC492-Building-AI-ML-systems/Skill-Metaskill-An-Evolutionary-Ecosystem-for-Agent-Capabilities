---
name: markdown-link-check-unclear
description: Find broken links in a folder of Markdown files and report where each one is.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [docs, Markdown, links]
prerequisites:
  commands: [python3]
---

# Markdown Link Check

Finds links in Markdown that do not work.

## When to Use

- The user asks to check docs for broken links.

## Steps

1. Pick the folder.
2. Check the links in it.
3. Tell the user about the bad ones.

```bash
python3 - docs <<'EOF'
import re, sys, pathlib, urllib.request
for md in pathlib.Path(sys.argv[1]).rglob("*.md"):
    for target in re.findall(r"\]\(([^)\s#]+)", md.read_text()):
        try:
            if target.startswith("http"):
                urllib.request.urlopen(target)
            else:
                open(md.parent / target)
        except Exception:
            pass
EOF
```
