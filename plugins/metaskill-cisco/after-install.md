# metaskill-cisco is installed

`evaluate` and `rank` now also run Cisco's skill scanner for the Safety score, which
catches malicious skills (prompt injection, data exfiltration, malicious code) that
metaskill's own checks are not built for. Nothing else to configure.

**Restart Hermes** if it was running, so it can import the new scanner package.

To turn the add-on off, run `hermes plugins disable metaskill-cisco` and restart Hermes.
Once Hermes has removed the scanner from its environment, Safety is scored with metaskill's
own checks only, and the report marks it `partial`.
