# Safety severity scale: provenance

The safety score is set by the **worst finding's severity** ([findings.md](findings.md),
[evaluate-safety.md §5](../evaluate-safety.md)). This document explains where that
severity scale comes from, and — honestly — which parts are backed by an external
standard and which are our own policy.

It exists because a reviewer will ask "why is a HIGH finding worth `0.11` and not
`0.25`?" and "why is deleting a file worse than overwriting one?". Those are two
different questions about two different layers, and they have two different answers.

## The two layers

| Layer | Decides | Backed by | Status |
|---|---|---|---|
| **Ordinal** | which findings outrank which (`INFO < LOW < MEDIUM < HIGH < CRITICAL`, and which level a finding gets) | NIST FIPS-199 magnitude scale + a recoverability ordering principle | scale adopted; per-category rubric is validation work still owed |
| **Cardinal** | the `[0, 1]` number each level is worth | CVSS v3.1 qualitative band scale (FIRST.org) | implemented in `SEVERITY_SCORE` |

The ordinal layer answers "is this worse than that". The cardinal layer only turns
an already-decided level into the number the leaderboard needs. Keeping them separate
is what lets us change the numbers without re-labelling any finding, and vice versa.

## 1. Cardinal scale — CVSS bands (implemented)

[CVSS](https://www.first.org/cvss/) (Common Vulnerability Scoring System, maintained
by the nonprofit FIRST) is the industry-standard severity scale for security issues —
the `9.8 Critical` you see on a CVE is a CVSS score. Its v3.1 spec publishes a
**qualitative severity rating scale** that buckets the 0–10 score into named bands:

| CVSS band | Numeric range |
|---|---|
| None | 0.0 |
| Low | 0.1 – 3.9 |
| Medium | 4.0 – 6.9 |
| High | 7.0 – 8.9 |
| Critical | 9.0 – 10.0 |

We have the band (our finding's severity level) and need a number — the inverse of
what CVSS computes. So we **invert the published scale**: take the top of each band
(its worst case) and flip CVSS's "higher = worse" into our "higher = safer" with
`safety = 1 − cvss/10`.

| Level | CVSS band ceiling | `1 − cvss/10` | `SEVERITY_SCORE` |
|---|---|---|---|
| INFO | None (0.0) | 1 − 0.0 | **1.00** |
| LOW | 3.9 | 1 − 0.39 | **0.61** |
| MEDIUM | 6.9 | 1 − 0.69 | **0.31** |
| HIGH | 8.9 | 1 − 0.89 | **0.11** |
| CRITICAL | 10.0 | 1 − 1.0 | **0.00** |

Two deliberate choices, both policy:

- **Band ceiling, not floor or midpoint.** This is the one "curve shape" decision we
  own. For a safety *gate* the conservative reading is correct: a HIGH finding *could*
  be as bad as 8.9, so it is scored as if it is. A nice consequence is that CRITICAL's
  band ceiling (10.0) already lands on `0.00`, so the derivation and the hard gate
  below agree instead of one overriding the other.
- **CRITICAL = 0 is a hard gate.** A CRITICAL finding gives the lowest possible safety
  score regardless of anything else. It does not remove the skill from the ranking
  ([evaluate.md §5](../evaluate.md)).
- **INFO = 1.0, the same as a clean skill.** This is deliberate: CVSS puts an
  informational note (None) in its own band, separate from Low. An INFO finding flags
  something worth recording, not a cost to the user, so it does not lower the score. The
  `reason` line still names the finding ("Worst finding: info …"), so the report shows
  the difference between an INFO-only skill and a clean one.

**What CVSS does and does not give us.** It gives a *recognized, citable scale* — the
numbers are no longer free parameters; each is a documented function of a published
band. It does **not** give empirical truth: FIRST acknowledges the CVSS formula is an
expert curve-fit, not a measurement, and CVSS is built for software vulnerabilities,
not agent-skill behaviour. We borrow its **scale**, not its vectors — we do not compute
a CVSS vector per finding. The honest claim is "we adopt CVSS's band scale for spacing
and legibility; the mapping is our policy."

This is still stricter than the field. The numeric skill scanners we surveyed
([NVIDIA SkillSpector](https://github.com/NVIDIA/SkillSpector), ClawHub, community
scanners) all pick their band weights freely; we are the only one pinning the scale to
a published standard.

## 2. Ordinal ranking — FIPS-199 magnitude + recoverability (policy)

The cardinal scale above is only defensible if the *level* feeding it is itself
defensible. That is the ordinal layer: which finding gets CRITICAL vs HIGH vs MEDIUM.

**Level ordering** (`INFO < LOW < MEDIUM < HIGH < CRITICAL`) is universal across CVSS,
Cisco, SkillSpector and every scanner; it needs no justification beyond citing it.

**Which finding gets which level** is anchored to
[NIST FIPS-199](https://csrc.nist.gov/pubs/fips/199/final), the US federal standard for
security categorization. FIPS-199 rates impact on a three-level magnitude scale by the
severity of the adverse effect:

| FIPS-199 level | Adverse effect | Our level band |
|---|---|---|
| Low | limited | LOW / INFO |
| Moderate | serious | MEDIUM |
| High | severe or catastrophic | HIGH / CRITICAL |

FIPS-199 gives the *magnitude* axis. It does not, by itself, distinguish a reversible
harm from an irreversible one — so we add one **ordering principle** on top, consistent
with CVSS's integrity/availability impact metrics and NIST SP 800-61's *Recoverability*
incident category:

> At equal magnitude, an **irreversible** effect outranks a reversible one, and a
> **broader scope** outranks a narrower one.

This is the principle that answers "is one delete worse than another": permanently
deleting (`rm -rf`, unrecoverable) outranks a reversible edit, and a delete scoped to a
home directory outranks one scoped to a single temp file. The magnitude scale is
FIPS-199's; the recoverability refinement is a policy we adopt, not something FIPS-199
asserts.

### Per-category rubric (validation work still owed)

Turning the principle above into a defensible level *per finding category* means
annotating each of our categories with its impact rationale. The intended shape:

| Category | Magnitude (FIPS-199) | Recoverable? | Scope | CWE / OWASP tag | Level |
|---|---|---|---|---|---|
| `destructive-unconfirmed` (e.g. `rm -rf ~`) | High — severe | No | broad | CWE-77 / LLM06 | CRITICAL |
| `data_exfiltration` | High — severe | No (data already sent) | external | CWE-200 / LLM02 | HIGH |
| warning without danger wording | Low — limited | — | — | — | LOW |

> **Status:** this table is a *schema*, not a completed annotation. Only the levels
> already emitted by [checks.py](checks.md) and the Cisco adapter ([cisco.md](cisco.md))
> are in force today; a full per-category rationale with CWE/OWASP tags is outstanding
> work, tracked as an open question in [evaluate-safety.md §10](../evaluate-safety.md).

## 3. What is backed vs. what we own

Stated plainly, so the doc cannot be read as over-claiming:

- **Backed by an external standard:** the level ordering (universal); the number scale
  (CVSS v3.1 bands); the magnitude axis for assigning levels (FIPS-199).
- **Our policy, declared not derived:** the band-ceiling curve shape; the
  recoverability/scope ordering principle; the `CRITICAL = 0` gate; the specific level
  assigned to each of our own careless-skill categories.
- **Not claimed by anyone, including us:** that the numbers are empirically measured.
  No source in this space provides that; see the survey in
  [evaluate-safety.md §3.4](../evaluate-safety.md).

## 4. Reproducibility — the one empirical check (planned)

Everything above backs the scale by *authority*. The only property that can be backed
*empirically* is reproducibility: whether independent raters assign the same level to
the same finding. The measure is **inter-rater reliability** (Cohen's κ) between the
LLM review, the deterministic checks, and a human pass over the
[golden dataset](../evaluation/golden_dataset.md) labels.

This does not prove the ranking is "correct" — nothing can — but it proves the labels
are *consistent and reproducible*, which is the property an auditor actually asks for,
and the one a freely-chosen numeric score (e.g. SkillSpector's opaque `93/100`) cannot
demonstrate. It is planned follow-up, not yet implemented.

## 5. How this is implemented

The policy above maps onto specific code. Nothing here computes a CVSS vector or a
FIPS-199 categorization at run time — the standards shape *constants*, and the code
looks those constants up.

### Cardinal: producing the number

The derivation is executable, not hand-typed, so the numbers can't drift from their
source. In [findings.py](../../metaskill/evaluate/safety/findings.py):

```python
_CVSS_BAND_CEILING = {            # top of each CVSS v3.1 band (FIRST.org)
    Severity.CRITICAL: 10.0,      # 9.0-10.0
    Severity.HIGH:      8.9,      # 7.0-8.9
    Severity.MEDIUM:    6.9,      # 4.0-6.9
    Severity.LOW:       3.9,      # 0.1-3.9
    Severity.INFO:      0.0,      # None
}
SEVERITY_SCORE = {level: round(1.0 - ceiling / 10, 2)      # invert: higher = safer
                  for level, ceiling in _CVSS_BAND_CEILING.items()}
# -> CRITICAL 0.0, HIGH 0.11, MEDIUM 0.31, LOW 0.61, INFO 1.0
```

To change the curve shape (e.g. band *floor* instead of ceiling), you edit the one
`_CVSS_BAND_CEILING` table; the five scores follow. The number for a skill is then a
single lookup on the worst finding:

```python
def score_from_findings(findings):          # findings.py
    worst = worst_severity(findings)        # most severe SAFETY finding, or None
    return 1.0 if worst is None else SEVERITY_SCORE[worst]
```

`worst_severity` ignores findings routed to other dimensions, so a secret sent to
Configuration Cleanliness never lowers the safety score. `1.0` means "backends ran and
found nothing that lowers the score" — no findings, or only INFO notes (INFO scores 1.0).
[`__init__.py`](score.md) checks that a backend actually ran before calling this, so an
all-skipped scan scores `null`, never `1.0`.

### Ordinal: setting the level

The ordering used to pick "worst" and to merge duplicates is one table in
[findings.py](../../metaskill/evaluate/safety/findings.py):

```python
SEVERITY_RANK = {CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0}
```

Each finding's *level* is set by the backend that raises it:

- **Cisco** ([cisco.py](cisco.md), `_convert_finding`): `Severity(f["severity"])` — Cisco's
  own level is passed through unchanged. For Cisco findings the ordinal is Cisco's, and
  we only normalize the enum.
- **Own checks** ([checks.py](checks.md)): each rule carries a fixed `Severity` chosen
  per the FIPS-199 magnitude + recoverability policy of §2. This is where our ordinal
  policy actually lives. The clearest example is the delete-target table:

  ```python
  _KIND_SEVERITY = {
      "system":         Severity.CRITICAL,   # irreversible, broad
      "empty-variable": Severity.CRITICAL,   # e.g. `rm -rf "$VAR"/` with VAR unset -> "/"
      "symlink":        Severity.CRITICAL,
      "unknown":        Severity.HIGH,        # not known-recoverable
      "rebuildable":    Severity.MEDIUM,      # recoverable (e.g. node_modules)
  }
  ```

  A *recoverable* delete is MEDIUM; an *irreversible or broad* one is HIGH/CRITICAL —
  the recoverability principle, in code. Other rule tables (destructive commands,
  credential exposure, untrusted sources) pin their levels the same way.
- **LLM review** ([review.py](review.md)): the judge returns a level per finding; the
  quote-verified finding keeps it. *(Pending: `review.py` lands with MET-21; until then the
  deep tier has no own-checks review, only Cisco's LLM analyzer.)*

`merge` keeps the most severe level when two backends report the same problem at the
same location, and `worst_severity` then selects the single worst across everything.
That worst level is the only one the cardinal step above ever sees.

## References

- CVSS v3.1 specification and qualitative severity rating scale — <https://www.first.org/cvss/>
- NIST FIPS-199, *Standards for Security Categorization* — <https://csrc.nist.gov/pubs/fips/199/final>
- NIST SP 800-61, *Computer Security Incident Handling Guide* (Recoverability) — <https://csrc.nist.gov/pubs/sp/800/61/r2/final>
- CWE (Common Weakness Enumeration) — <https://cwe.mitre.org/>
- OWASP Top 10 for LLM Applications — <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
