---
name: meeting-action-items
description: Turn a meeting transcript or rough notes into a short list of decisions and action items.
version: 1.0.0
author: Metaskill golden dataset (synthetic)
license: MIT
metadata:
  hermes:
    tags: [meetings, notes, productivity]
---

# Meeting Action Items

Long meeting notes hide the parts people actually need: what was decided and who is doing what. This skill pulls those out into one short page.

## When to Use

- The user shares a transcript or notes and asks for a summary, decisions, follow-ups, or "what do I need to do".

## When NOT to Use

- The user wants a full written record of the meeting. Summarizing would drop detail they asked to keep.

## Steps

1. Read the whole transcript before writing anything.
2. List each **decision**: something the group agreed on. Write it as one plain sentence.
3. List each **action item** in the form *Owner — task — due date*. If no owner or date was said, write "unassigned" or "no date". Do not invent one.
4. List **open questions**: topics raised but not settled.
5. Return the result in this layout:

   ```markdown
   ## Decisions
   - ...

   ## Action items
   - Priya — send the revised budget — Friday

   ## Open questions
   - ...
   ```

## Checking the Result

- Every action item must trace back to something said in the transcript. If you cannot point to the line, remove it.
- If a section is empty, write "None" rather than leaving it out, so the reader knows it was checked.

## If Something Goes Wrong

- **Speakers are not labelled:** keep the action items but mark owners as "unclear" and tell the user.
- **The transcript is cut off:** say where it ends and that items after that point may be missing.
