---
name: ab-test-readout-unclear
description: Two-proportion z-test readout with NHST interpretation for binary-outcome RCTs.
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

Computes a pooled two-proportion z-statistic under H0: p_A = p_B against a two-sided H1 and reports the p-value at α = 0.05.

## Inputs

n_A, x_A, n_B, x_B (Bernoulli trials and successes per arm).

## Steps

1. Assume i.i.d. Bernoulli outcomes, SUTVA, and that the normal approximation holds (n·p̂ and n·(1−p̂) ≥ 10).
2. Compute p̂ for each arm, the pooled p̂, SE = √(p̂(1−p̂)(1/n_A + 1/n_B)), z = (p̂_B − p̂_A)/SE, and p = 2(1 − Φ(|z|)).

   ```bash
   python3 - 5000 400 5000 460 <<'EOF'
   import sys, math
   va, ca, vb, cb = map(int, sys.argv[1:])
   pa, pb = ca / va, cb / vb
   pooled = (ca + cb) / (va + vb)
   se = math.sqrt(pooled * (1 - pooled) * (1 / va + 1 / vb))
   z = (pb - pa) / se
   print(z, math.erfc(abs(z) / math.sqrt(2)))
   EOF
   ```

3. Reject H0 if p < α; otherwise fail to reject. Report the absolute lift and the Wald CI.
4. Flag Type I error inflation under optional stopping (peeking); recommend an alpha-spending function (O'Brien–Fleming) or an SPRT/mSPRT if sequential monitoring was used.
5. Note that statistical significance ≠ practical significance; compare against the MDE from the a priori power analysis (1 − β = 0.8).
