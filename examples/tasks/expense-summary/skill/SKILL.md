---
name: expense-summary
description: Total a CSV file of expenses by category and report the largest one.
---

# Expense summary

Summarize an expense file with columns `date`, `category`, `amount` and `status`.

1. Read the whole file with Python's `csv` module. Do not total it by hand.
2. Leave out every row whose `status` is `refunded`.
3. Add the remaining amounts by category with `decimal.Decimal`, so the totals do not
   pick up rounding errors. Keep the file's units: `12.50` plus `1.25` is written as
   `13.75`.
4. Write `summary.json` in the folder you were asked to work in, with the keys the request
   names.
5. Read `summary.json` back and compare its total with the sum of its categories before
   you answer.
6. Answer in one or two sentences: the total, and the category with the most spending.
