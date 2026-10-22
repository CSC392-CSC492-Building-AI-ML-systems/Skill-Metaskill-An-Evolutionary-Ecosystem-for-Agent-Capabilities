"""Trusted pilot oracle/verifier; never executes candidate code or model calls."""
import json


def expected_report(tickets, organization):
    """Compute the contract result; raise for invalid evaluator configuration."""
    if not isinstance(organization, str) or not organization.strip():
        raise ValueError("organization must contain a non-whitespace character")
    if not isinstance(tickets, list):
        return {"error": "invalid_input"}
    totals = {}
    for ticket in tickets:
        if not isinstance(ticket, dict):
            return {"error": "invalid_input"}
        team = ticket.get("team")
        status = ticket.get("status")
        minutes = ticket.get("minutes")
        if (not isinstance(team, str) or not team.strip()
                or status not in ("open", "closed", "cancelled")
                or type(minutes) is not int or minutes < 0):
            return {"error": "invalid_input"}
        if status != "cancelled":
            count, duration = totals.get(team, (0, 0))
            totals[team] = (count + 1, duration + minutes)
    teams = [{"team": team, "ticket_count": count, "minutes": duration}
             for team, (count, duration) in sorted(totals.items())]
    return {"organization": organization, "teams": teams,
            "total_minutes": sum(row["minutes"] for row in teams)}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-standard JSON constant: " + value)


def _same(actual, expected):
    # Python equates True with 1 and 1.0 with 1; the contract does not.
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same(a, e) for a, e in zip(actual, expected))
    return actual == expected


def assess_output(text, expected):
    """Return (passed, reason). Expected is a trusted, reviewed JSON object."""
    try:
        actual = json.loads(text, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        return False, "Output is not one strict JSON value."
    if not _same(actual, expected):
        return False, "Output differs from the reviewed expected JSON."
    return True, "Output matches the reviewed expected JSON."
