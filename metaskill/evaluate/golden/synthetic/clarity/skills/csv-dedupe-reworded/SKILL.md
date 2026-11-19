---
name: csv-dedupe-reworded
description: Delete repeated rows from a CSV file and say how many were deleted.
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

Deletes rows that occur more than once in a CSV file, leaving one copy of each record. The input file stays as it is; the result is saved as a new file beside it.

## When to Use

- The user wants duplicate rows taken out of a CSV file.
- The user asks how many rows in a CSV appear more than once.

## When Not to Use

- The file is in another format, such as Excel `.xlsx`. Ask the user to save it as CSV first.
- The user wants rows that are merely *alike* (for instance "Jon Smith" and "John Smith") combined. Only identical rows are removed here.

## Steps

1. Check the file path with the user and make sure the file is there.
2. Ask whether rows count as duplicates when every column matches, or only some columns (such as `email`). If the user has no preference, use every column.
3. Run this script, putting in the file name and, if needed, the column list:

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

4. Give the user the three numbers from the script's output and the location of the new file.

## Verifying the Result

The rows removed plus the rows out must equal the rows in. If the numbers do not add up, tell the user and keep the file back.

## Troubleshooting

- **File not found:** ask the user for the right path rather than guessing.
- **`KeyError` naming a column:** that column name does not exist. Show the user the header row and let them choose again.
- **Strange characters in the output:** the file is likely not UTF-8. Ask which program created it before trying again.
