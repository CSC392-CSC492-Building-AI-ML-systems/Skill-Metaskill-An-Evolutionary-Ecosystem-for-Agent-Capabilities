---
name: monthly-spending-review
description: Sort a month of bank transactions into categories and show where the money went.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [budgeting, personal finance]
---

# Monthly Spending Review

Bank statements list every purchase but rarely show the big picture. This skill groups a month of transactions into a few categories so the user can see where their money went and what changed from last month.

## When to Use

- The user shares a bank or card export (usually CSV) and asks about their spending or budget.

## When NOT to Use

- The user wants investment, tax, or debt advice. Say this skill only summarizes past spending.

## Steps

1. Open the file and find the date, description, and amount columns. If you are not sure which column is which, show the user the first three rows and ask.
2. Keep only spending (money going out). Leave out transfers between the user's own accounts.
3. Put each transaction in one of these categories: Housing, Groceries, Eating out, Transport, Bills and subscriptions, Shopping, Other. Use the description to decide (for example "UBER" goes under Transport).
4. Show a table, largest category first:

   | Category | Total | Share |
   |---|---|---|
   | Housing | $1,450 | 41% |
   | Groceries | $520 | 15% |

5. Below the table, point out the three largest single purchases and any subscription that appears for the first time.
6. If the user also gives last month's file, add a column showing the change for each category.

## Checking the Result

The category totals must add up to the total spending for the month. If they do not, find the missing transactions before showing the table.

## If Something Goes Wrong

- **Amounts use two columns (debit and credit) instead of one:** treat the debit column as spending.
- **Many transactions land in Other:** list the ten largest ones and ask the user where they belong.

Never send the transactions anywhere outside this conversation; they are private.
