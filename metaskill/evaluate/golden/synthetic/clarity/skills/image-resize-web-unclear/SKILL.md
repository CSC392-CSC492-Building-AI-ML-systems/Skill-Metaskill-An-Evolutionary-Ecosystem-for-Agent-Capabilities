---
name: image-resize-web-unclear
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

## Steps

1. Report how many files were processed and the total size before and after:

   ```bash
   du -sh photos photos-web
   ```

2. Resize every JPEG and PNG:

   ```bash
   magick mogrify -path photos-web -resize '1600x>' -quality 82 photos/*.jpg photos/*.png
   ```

3. Ask for the folder of images and the widest size they need.

## Output folder

The output goes in `photos-web`, which should be created at some point before step 2 (see below).

## When to Use

When images need to be smaller. Also see the section on printing, further down.

## Making the folder

`mkdir -p photos-web`. This must happen before the resize or nothing is written, which is why it is mentioned here after the steps.

## Before You Start

ImageMagick must be installed. This should be checked before step 1.

## Printing

Not for print. Mentioned earlier under When to Use.
