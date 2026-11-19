---
name: backyard-bird-log
description: Help the user identify a bird they saw from a description and add the sighting to their bird log.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [birds, nature, journaling]
---

# Backyard Bird Log

Helps a casual birdwatcher put a name to a bird they saw and keeps a simple record of their sightings. Use everyday bird names (such as "American robin"), not scientific names, unless the user asks for them.

## When to Use

- The user describes a bird they saw and wants to know what it was.
- The user wants to add a sighting to their log or look back at past sightings.

## When NOT to Use

- The bird is injured or in danger. Point the user to a local wildlife rescue instead of identifying it.

## Steps

1. Ask for the four things that matter most, if the user has not given them:
   - **Size**, compared with a familiar bird (sparrow, robin, crow).
   - **Main colours** and any standout markings (a red chest, a white stripe over the eye).
   - **Where** they were, roughly (city or region) and **when** (month).
   - **What it was doing** (on the ground, at a feeder, climbing a tree trunk).
2. Suggest up to three likely birds, most likely first. For each, give one feature the user can check to tell it apart from the others.
3. Ask the user which one matches. If none do, ask about the feature that would separate them best, then try again.
4. Once the user picks one, add a line to `bird-log.csv` in their notes folder:

   ```csv
   date,bird,place,notes
   2026-05-14,Northern cardinal,Backyard feeder,Pair seen together
   ```

   Create the file with that header row if it does not exist yet.

## Checking the Result

Read back the line you added and confirm the date and bird name with the user.

## If Something Goes Wrong

- **The description is too vague to narrow down:** say so plainly and ask for a photo or one more detail, rather than guessing.
- **The suggested bird does not live in that region at that time of year:** drop it from the list.
