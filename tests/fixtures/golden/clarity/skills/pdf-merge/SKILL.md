---
name: pdf-merge
description: Combine several PDF files into one, in the order the user chooses.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [PDF, documents]
prerequisites:
  commands: [qpdf]
---

# Merge PDFs

Joins two or more PDF files into a single PDF. The originals are left untouched.

## When to Use

- The user wants to combine PDFs, for example to send one attachment instead of several.

## When NOT to Use

- The user wants to edit text inside a PDF. Merging does not change page content.

## Steps

1. Check that `qpdf` is installed with `qpdf --version`. If it is not, ask the user to install it and stop.
2. List the files the user gave you, numbered, and ask them to confirm the order. The merged PDF follows this order exactly.
3. Ask for the name of the new file. Default to `merged.pdf` in the same folder as the first input.
4. Merge:

   ```bash
   qpdf --empty --pages first.pdf second.pdf third.pdf -- merged.pdf
   ```

## Checking the Result

The page count of the new file must equal the sum of the page counts of the inputs:

```bash
qpdf --show-npages merged.pdf
```

Tell the user the final page count and where the file was saved.

## If Something Goes Wrong

- **"file is damaged" or "invalid password":** one input is broken or locked. Tell the user which file it was. Do not try to remove a password unless the user owns the file and gives you the password.
- **The output file already exists:** ask before overwriting it.
