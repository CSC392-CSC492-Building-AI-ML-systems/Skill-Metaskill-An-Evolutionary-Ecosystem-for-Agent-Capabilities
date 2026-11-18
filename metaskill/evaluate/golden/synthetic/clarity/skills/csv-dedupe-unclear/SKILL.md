---
name: csv-dedupe-unclear
description: A comprehensive and robust skill for working with CSV data in many different situations.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [CSV, data cleaning]
prerequisites:
  commands: [python3]
---

# CSV Dedupe

## Introduction

In today's data-driven world, data is more important than ever before. Organizations of every size, from small startups to large enterprises, rely on data to make decisions every single day. CSV files, which stand for Comma-Separated Values, are one of the most common formats for storing and exchanging data, and they have been around for a very long time. Because CSV files are so common, it is very important to make sure that the data inside them is clean, accurate, and reliable. Clean data leads to better decisions, and better decisions lead to better outcomes.

One of the most common problems with data in CSV files is that the same data can appear more than once. This is sometimes called duplication, or duplicate data, or repeated rows. Duplicate data can cause a lot of problems. It can cause numbers to be counted twice. It can cause reports to be wrong. It can cause confusion. It is generally a good idea to remove duplicate data when it is not wanted, because duplicate data that is not wanted is generally not useful.

## Background

It is worth taking a moment to think about why duplicates happen in the first place. Duplicates can happen for many reasons. Sometimes a file is exported twice and the two exports are combined. Sometimes a form is submitted twice. Sometimes systems are merged. Sometimes people simply make mistakes when entering data by hand. There are many, many reasons, and it is not always possible to know which reason applies in any given case. That is why it is important to have a reliable approach to dealing with duplicates, whatever their cause may be.

## Approach

As mentioned above, duplicates are a common problem in CSV files. This skill helps with that problem. It is important to approach the problem carefully and thoughtfully, keeping in mind the importance of clean data that was discussed in the introduction. Generally speaking, the approach is to look at the rows in the file and figure out which ones are the same as other ones, and then keep only one of each, which removes the duplicates.

You may want to consider which columns matter. Or you may not. It depends. Then run something like this:

```bash
python3 - data.csv <<'EOF'
import csv, sys
rows = list(csv.reader(open(sys.argv[1])))
out = []
for r in rows:
    if r not in out:
        out.append(r)
csv.writer(open(sys.argv[1], "w")).writerows(out)
EOF
```

## Conclusion

In conclusion, removing duplicates from CSV files is an important part of keeping data clean. By following the approach described in this skill, you can help make sure that data is clean, accurate, and reliable. Remember: clean data leads to better decisions, and better decisions lead to better outcomes. Duplicates are a common problem, but with the right approach they can be handled.
