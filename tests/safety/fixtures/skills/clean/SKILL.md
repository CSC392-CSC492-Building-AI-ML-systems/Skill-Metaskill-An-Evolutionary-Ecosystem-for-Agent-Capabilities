---
name: csv-summary
description: Summarize a CSV file into a short markdown report.
---

# CSV summary

Use this skill when the user asks for a summary of a CSV file.

1. Read the file the user names.
2. Count the rows and list the columns.
3. Write the summary to `summary.md` in the current folder.

```bash
python3 -c "import csv, sys; print(sum(1 for _ in csv.reader(open(sys.argv[1]))))" data.csv
```

If the file does not exist, tell the user and stop.
