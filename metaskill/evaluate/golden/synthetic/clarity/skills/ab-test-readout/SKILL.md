---
name: ab-test-readout
description: Explain in plain language whether an A/B test result shows a real difference between two versions.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [experiments, statistics, product]
prerequisites:
  commands: [python3]
---

# A/B Test Readout

An A/B test shows two versions of something (say, two sign-up pages) to different visitors and compares how often each one works. This skill answers the question people actually ask afterwards: "Is B really better, or could the difference be luck?"

## When to Use

- The user has visitor and conversion counts for two versions and wants to know which one won.

## When NOT to Use

- There are more than two versions, or the result is an average (like time on page) rather than a count of successes. Tell the user this skill does not cover that case.

## What You Need

For each version: the number of **visitors** and the number of **conversions** (visitors who did the thing being measured, such as signing up).

## Steps

1. Get the four numbers from the user. Check that conversions are never larger than visitors.
2. Run the comparison:

   ```bash
   python3 - 5000 400 5000 460 <<'EOF'
   import sys, math
   va, ca, vb, cb = map(int, sys.argv[1:])
   pa, pb = ca / va, cb / vb
   pooled = (ca + cb) / (va + vb)
   se = math.sqrt(pooled * (1 - pooled) * (1 / va + 1 / vb))
   z = (pb - pa) / se
   p = math.erfc(abs(z) / math.sqrt(2))
   print(f"A: {pa:.2%}  B: {pb:.2%}  change: {pb - pa:+.2%}  p-value: {p:.3f}")
   EOF
   ```

3. Explain the result in plain words:
   - The **p-value** is roughly the chance of seeing a difference this big if the two versions were actually the same. Below 0.05 is the usual bar for "probably a real difference".
   - Below 0.05: "B's rate is higher, and luck alone is unlikely to explain it."
   - 0.05 or above: "We can't tell yet. The difference could be luck. More visitors would help."
4. Always state the size of the change (for example "+1.2 percentage points"), not just whether it passed.

## If Something Goes Wrong

- **Fewer than about 100 visitors or 10 conversions per version:** the numbers are too small for this method. Say so instead of reporting a p-value.
- **The user checked the result many times and stopped when it looked good:** warn that this makes a false win more likely.
