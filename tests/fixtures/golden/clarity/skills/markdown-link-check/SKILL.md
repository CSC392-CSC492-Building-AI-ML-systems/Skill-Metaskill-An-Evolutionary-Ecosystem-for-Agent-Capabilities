---
name: markdown-link-check
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

Documentation goes stale when files move or websites change. This skill finds links that no longer work so they can be fixed before readers hit them.

## When to Use

- The user asks to check docs, a README, or a notes folder for broken links.

## When NOT to Use

- The site is behind a login. The check cannot see those pages and would report them all as broken.

## Steps

1. Ask which folder to check. Default to the current folder.
2. Ask whether to check web links too. Local file links are always checked; web links need an internet connection and take longer.
3. Run the check (add `--web` to include web links):

   ```bash
   python3 - docs --web <<'EOF'
   import re, sys, pathlib, urllib.request
   root, web = pathlib.Path(sys.argv[1]), "--web" in sys.argv
   for md in root.rglob("*.md"):
       for n, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
           for target in re.findall(r"\]\(([^)\s#]+)", line):
               if target.startswith(("http://", "https://")):
                   if not web:
                       continue
                   try:
                       urllib.request.urlopen(urllib.request.Request(target, method="HEAD"), timeout=10)
                   except Exception as e:
                       print(f"{md}:{n}  {target}  ({e})")
               elif not (md.parent / target).exists():
                   print(f"{md}:{n}  {target}  (file not found)")
   EOF
   ```

4. Group the results by file and show them to the user. Each line gives the file, line number, link, and reason.

## Checking the Result

If nothing is printed, every checked link works. Say clearly whether web links were included, so "no broken links" is not misread.

## If Something Goes Wrong

- **Many web links fail with "403" or "429":** the site is blocking automated checks, not necessarily broken. List them separately as "could not verify".
- **The check is very slow:** run it without `--web` first, then check web links for one file at a time.
