---
name: photo-rename-by-date
description: Rename a folder of photos to the date and time each was taken, so they sort in order.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [photos, files, organizing]
prerequisites:
  commands: [exiftool]
---

# Rename Photos by Date

Photos from different phones and cameras have names like `IMG_4031.JPG` that do not sort by when they were taken. This skill renames them to the date and time they were taken, such as `2026-05-14_093012.jpg`, so any file list shows them in order.

## When to Use

- The user wants photos from one or more devices in date order, or wants file names that show the date.

## When NOT to Use

- The photos are scans or screenshots. They usually have no "date taken" stored in them, so the new names would be wrong.

## Before You Start

ExifTool must be installed. Check with `exiftool -ver`. If it is missing, ask the user to install it and stop until they have.

## Steps

1. Ask for the folder. Copy it to a new folder named `<folder>-renamed` and work only on the copy.
2. Preview the new names without changing anything:

   ```bash
   exiftool -d '%Y-%m-%d_%H%M%S%%-c.%%le' '-testname<DateTimeOriginal' photos-renamed
   ```

   The `%-c` part adds `-1`, `-2` to photos taken in the same second, so no file is overwritten.
3. Show the user the first ten lines of the preview and ask them to confirm.
4. Rename, using the same command with `-filename` in place of `-testname`:

   ```bash
   exiftool -d '%Y-%m-%d_%H%M%S%%-c.%%le' '-filename<DateTimeOriginal' photos-renamed
   ```

## Checking the Result

- The renamed folder has the same number of files as the original folder.
- ExifTool prints how many files it updated. Any file it could not rename is listed with a warning; tell the user which ones.

## If Something Goes Wrong

- **A photo keeps its old name:** it has no "date taken" stored. Leave it as it is and list it for the user.
- **The times are off by a few hours:** the camera clock was set to another time zone. Ask the user how many hours, shift the dates with `exiftool "-AllDates+=3" photos-renamed` (use `-=` for earlier), and rename again.
