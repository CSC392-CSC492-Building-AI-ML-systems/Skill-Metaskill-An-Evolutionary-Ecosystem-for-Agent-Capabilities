# Ticket summary task contract

Input is a JSON array of tickets. Each ticket must have a string team containing at least one non-whitespace character, status equal to "open", "closed", or "cancelled", and a nonnegative integer minutes (Boolean values are invalid). Extra input fields are ignored. Validate every row, including cancelled rows. If any row or the root is invalid, return exactly {"error":"invalid_input"} with no partial totals. For valid input, ignore cancelled tickets; return {"organization":<configured organization>,"teams":[{"team":<team>,"ticket_count":<integer>,"minutes":<integer>}],"total_minutes":<integer>}. Include every team with at least one non-cancelled ticket, sorted by team using Unicode code point order; do not trim, lowercase, or otherwise normalize team names. Empty input and cancelled-only input return an empty teams array and total_minutes 0. Return only the JSON object. Do not modify any source file. Do not access the network.

This public task contract is provided to both baseline and with-skill runs. Gold answers and annotations stay outside the agent workspace.


Organization configuration must contain at least one non-whitespace character and is preserved exactly, without trimming. Missing or invalid organization configuration returns exactly {"error":"invalid_config"} before ticket validation. In this pilot, whitespace follows Python str.isspace semantics; cross-validator Unicode whitespace compatibility must be reviewed before non-ASCII configuration cases are added.
