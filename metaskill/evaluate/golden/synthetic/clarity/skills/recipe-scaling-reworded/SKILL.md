---
name: recipe-scaling-reworded
description: Change the ingredient quantities in a recipe to serve a different number of people.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [cooking, recipes]
---

# Recipe Scaling

Adjusts a recipe to make more or fewer servings, rounding quantities to amounts a home cook can really measure.

## When to Use

- The user wants an existing recipe for more or fewer people.

## When Not to Use

- The user wants to replace ingredients (for instance to make it dairy-free). That is substitution, not scaling.

## Steps

1. Find how many servings the recipe makes. If the recipe does not say, ask the user.
2. Calculate the scale factor by dividing the new servings by the original servings. Going from 4 servings to 6 gives a factor of 1.5.
3. Multiply each ingredient quantity by the factor.
4. Round to amounts that are easy to measure in a kitchen:
   - Spoons and cups to the closest ¼ (1.4 cups becomes 1½ cups).
   - Eggs to whole eggs. When the result falls between two whole numbers, say so (for instance "3 eggs, or 2 eggs plus 1 yolk").
   - Grams to the closest 5 g.
5. Give back the whole recipe with the new quantities, with the new number of servings at the top.

## What Does Not Scale Evenly

Mention these to the user instead of quietly changing them:

- **Salt and strong spices:** when the factor is above 2, begin with roughly three quarters of the scaled amount and taste.
- **Baking time:** a larger dish usually takes longer, but not twice as long. Keep the original time and tell the user to check early and often.
- **Pan size:** recommend a pan size that suits the new quantity.

## Verifying the Result

Pick two ingredients and compare them in the original and the new version. The ratio between them should not have changed.
