---
name: backyard-bird-log-unclear
description: Passerine and near-passerine field identification with eBird-compatible checklist logging.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [birds, nature, journaling]
---

# Backyard Bird Log

Assists with field ID of Passeriformes and allied orders and records the observation.

## Steps

1. Elicit GISS (jizz), bill morphology (conical, pointed, decurved, hooked), and plumage topography: presence of supercilium, eyering, malar or submoustachial stripe, wingbars on the greater and median coverts, tertial edging, primary projection, and rectrix pattern.
2. Establish phenology and range: compare against the relevant BBS stratum and eBird frequency bar charts for the county and week.
3. Generate a differential. Typical confusion sets:
   - *Turdus migratorius* vs. *Ixoreus naevius* (check breast band and wing pattern).
   - *Haemorhous mexicanus* vs. *H. purpureus* (check supercilium, streaking on the undertail coverts, and culmen shape).
   - *Poecile atricapillus* vs. *P. carolinensis* (check greater covert edging and bib margin; consider hybrid zone).
   - *Spizella passerina* vs. *S. pallida* in basic plumage (check lores and auricular patch).
4. Consider age and sex classes, molt timing (preformative vs. definitive basic), and leucism.
5. Assign the taxon at the lowest confident rank; use a slash or spuh otherwise.
6. Append to `bird-log.csv` with columns `date,taxon,locality,breeding_code,notes` using the binomial.
