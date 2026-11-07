---
name: guide
description: How to run the metaskill tool - find the skill's path, pick generalize or personalize, find the config.
metadata:
  hermes:
    requires_tools: [metaskill]
---

# Using the metaskill tool

`metaskill` turns a Hermes skill into a shareable template (**generalize**) or fills a
template with a user's values (**personalize**). Follow these steps in order.

## 1. Resolve the skill to a path

The tool needs a path, not a name. If the user gave a skill name (for example
`gcp-file-transfer`):

1. Call `skill_view("<name>")`. Its result includes `skill_dir`; pass that as `skill_path`.
2. If `skill_view` cannot find it, call `skills_list` and look for close matches.

If no skill matches, or more than one could be meant, ask the user which one. Do not guess.
If the user gave a path instead of a name, use it as is.

## 2. Confirm the path exists

Check that the directory or file is there before calling the tool. If the tool replies
`skill_path does not exist`, go back to step 1. Do not retry with the same value.

## 3. Choose the operation

- **generalize**: the user wants to share, publish, template or anonymize a skill, or to
  remove their personal values (keys, emails, paths, hostnames, org names) from it.
- **personalize**: the user has a generalized skill (a template with placeholders) and
  wants it filled in with their own values.

If the request is unclear, ask the user which one they want.

## 4. For personalize, find the config

`personalize` needs `config_path`, a JSON file with the user's values.

1. Look next to the generalized skill for a filled-in config such as `config.json`.
   `config.example.json` holds only example values, so it is not the user's config.
2. If there is no config, tell the user the generalized skill includes
   `config.schema.json` and `config.example.json`, and ask them to fill in a copy.
   Never invent values.

## 5. Call the tool

```
metaskill(operation="generalize", skill_path="<dir>")
metaskill(operation="personalize", skill_path="<dir>", config_path="<file>")
```

`output_path` is optional and defaults to `./metaskill-output/<skill>-template` or
`./metaskill-output/<skill>-personalized`. The source skill is never modified.

## 6. Report the result

The response contains counts, artifact paths, `unresolved` items and `warnings`. It never
contains the detected values themselves. Tell the user where the output was written, how
many values were found or substituted, and anything left unresolved. Do not open the
outputs to quote the user's personal values back to them.

If `status` is `partial`, the job did not finish, and there may be no output. Pass on
`message` and `warnings`, and do not say the skill was generalized or personalized.
