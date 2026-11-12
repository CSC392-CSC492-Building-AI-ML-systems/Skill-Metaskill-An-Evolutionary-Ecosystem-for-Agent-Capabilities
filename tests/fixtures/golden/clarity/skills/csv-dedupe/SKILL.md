---
name: csv-dedupe
description: Remove duplicate rows from a CSV file and report how many were removed.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [CSV, data cleaning]
prerequisites:
  commands: [python3]
---

# CSV Dedupe

Removes repeated rows from a CSV file so each record appears once. The original file is never changed; the cleaned copy is written next to it.

## When to Use

- The user asks to remove duplicates from a CSV file.
- The user wants to know how many rows in a CSV are repeated.

## When NOT to Use

- The file is not a CSV (for example Excel `.xlsx`). Ask the user to export it as CSV first.
- The user wants to merge rows that are only *similar* (such as "Jon Smith" and "John Smith"). This skill only removes exact matches.

## Steps

1. Confirm the file path with the user and check that it exists.
2. Ask whether duplicates should be judged on every column or only on certain columns (for example `email`). Default to every column.
3. Run the script, replacing the file name and the optional column list:

   ```bash
   python3 - data.csv email <<'EOF'
   import csv, sys
   path, keys = sys.argv[1], sys.argv[2:]
   with open(path, newline="") as f:
       reader = csv.DictReader(f)
       rows, seen, out = list(reader), set(), []
       for row in rows:
           key = tuple(row[k] for k in keys) if keys else tuple(row.values())
           if key not in seen:
               seen.add(key)
               out.append(row)
   target = path.rsplit(".", 1)[0] + "_deduped.csv"
   with open(target, "w", newline="") as f:
       writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
       writer.writeheader()
       writer.writerows(out)
   print(f"{len(rows)} rows in, {len(out)} rows out, {len(rows) - len(out)} removed -> {target}")
   EOF
   ```

4. Tell the user the three numbers the script printed and where the new file is.

## Checking the Result

The rows-out count plus the removed count must equal the rows-in count. If they do not, say so and do not hand over the file.

## If Something Goes Wrong

- **File not found:** ask the user for the correct path. Do not guess.
- **`KeyError` for a column:** the column name is wrong. Show the user the header row and ask them to pick again.
- **Garbled characters:** the file is probably not UTF-8. Ask the user what program produced it before retrying.
