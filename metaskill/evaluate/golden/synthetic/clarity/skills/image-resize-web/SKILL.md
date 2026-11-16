---
name: image-resize-web
description: Shrink a folder of photos so they load quickly on a website, without changing the originals.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [images, web, photos]
prerequisites:
  commands: [magick]
---

# Resize Images for the Web

Photos straight from a phone or camera are often several megabytes each, which makes web pages slow. This skill makes smaller copies that still look sharp on screen.

## When to Use

- The user wants to make images smaller for a website, blog, or email.

## When NOT to Use

- The images are for printing. Print needs the full-size originals.

## Before You Start

ImageMagick must be installed. Check with `magick -version`. If it is missing, ask the user to install it (`brew install imagemagick` on macOS, `winget install ImageMagick.ImageMagick` on Windows) and stop until they have.

## Steps

1. Ask for the folder of images and the widest size they need. Default to 1600 pixels wide, which suits most websites.
2. Make an output folder next to the original one, named `<folder>-web`. Never write into the original folder.
3. Resize every JPEG and PNG. Images already narrower than the target are copied without being enlarged:

   ```bash
   mkdir -p photos-web
   magick mogrify -path photos-web -resize '1600x>' -quality 82 photos/*.jpg photos/*.png
   ```

4. Report how many files were processed and the total size before and after:

   ```bash
   du -sh photos photos-web
   ```

## Checking the Result

- The output folder has the same number of images as the input folder.
- Open one or two of the new images and confirm they still look sharp.

## If Something Goes Wrong

- **"no images defined":** the folder has no `.jpg` or `.png` files. Check the file extensions (some cameras use `.jpeg` or `.JPG`) and adjust the pattern.
- **Images look blurry:** raise `-quality` to 90 and run again.
