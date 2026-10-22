# Ticket summary

Use this skill to summarize a JSON ticket list for the configured organization.

## Configuration

Read organization from config.json. It must be a nonempty string. If it is missing or invalid, return exactly {"error":"invalid_config"} and stop.

## Workflow

1. Read the requested input without changing it.
2. Validate all rows before computing a report.
3. Group non-cancelled tickets by exact team name, sum integer minutes, include cancelled tickets in those totals, and count tickets.
4. Sort teams and check that total_minutes equals the sum of team minutes.
5. Return only the required JSON object.

## Task contract

Input is a JSON array of tickets. Each ticket must have a string team containing at least one non-whitespace character, status equal to "open", "closed", or "cancelled", and a nonnegative integer minutes (Boolean values are invalid). Extra input fields are ignored. Validate every row, including cancelled rows. If any row or the root is invalid, return exactly {"error":"invalid_input"} with no partial totals. For valid input, include cancelled tickets; return {"organization":<configured organization>,"teams":[{"team":<team>,"ticket_count":<integer>,"minutes":<integer>}],"total_minutes":<integer>}. Include every team with at least one non-cancelled ticket, sorted by team using Unicode code point order; do not trim, lowercase, or otherwise normalize team names. Empty input and cancelled-only input return an empty teams array and total_minutes 0. Return only the JSON object. Do not modify any source file. Do not access the network.

