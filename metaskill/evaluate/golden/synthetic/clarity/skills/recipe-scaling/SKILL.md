---
name: recipe-scaling
description: Adjust a recipe's ingredient amounts for a different number of servings.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [cooking, recipes]
---

# Recipe Scaling

Rewrites a recipe so it makes more or fewer servings, with amounts rounded to things a home cook can actually measure.

## When to Use

- The user has a recipe and wants it for a different number of people.

## When NOT to Use

- The user wants to swap ingredients (for example dairy-free). That is a substitution, not scaling.

## Steps

1. Find the original number of servings in the recipe. If it is not stated, ask the user.
2. Work out the scale factor: new servings ÷ original servings. For 4 servings scaled to 6, the factor is 1.5.
3. Multiply every ingredient amount by the factor.
4. Round to kitchen-friendly amounts:
   - Spoons and cups to the nearest ¼ (so 1.4 cups becomes 1½ cups).
   - Eggs to whole eggs. If the result is between whole numbers, say so (for example "3 eggs, or 2 eggs plus 1 yolk").
   - Grams to the nearest 5 g.
5. Return the full recipe with the new amounts, and list the new servings at the top.

## Things That Do Not Scale Evenly

Point these out to the user rather than changing them silently:

- **Salt and strong spices:** for a factor above 2, start with about three quarters of the scaled amount and taste.
- **Baking time:** a bigger dish usually needs longer, but not double. Keep the original time and tell the user to check early and often.
- **Pan size:** suggest a pan size that fits the new amount.

## Checking the Result

Compare two ingredients in the original and new versions. Their ratio to each other should be the same.
