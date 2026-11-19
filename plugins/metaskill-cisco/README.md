# metaskill-cisco

An optional add-on for the [metaskill](../../README.md) plugin. It installs
[Cisco's skill scanner](https://pypi.org/project/cisco-ai-skill-scanner/)
(`cisco-ai-skill-scanner`, pinned to 2.2.1) for `evaluate` and `rank`.

metaskill's Safety score has two layers ([docs/evaluate-safety.md](../../docs/evaluate-safety.md)):
its own checks for *careless* skills (an unconfirmed `rm -rf`, a printed API key, a
`curl | sh`), and Cisco's scanner for *malicious* ones (prompt injection, data
exfiltration, malicious code). Without this add-on, Safety is scored with the own checks
only and marked `partial`, and most malicious skills go undetected; the tool says so in
its result and gives the command below.

## Install

```bash
hermes plugins install CSC392-CSC492-Building-AI-ML-systems/Skill-Metaskill-An-Evolutionary-Ecosystem-for-Agent-Capabilities#plugins/metaskill-cisco
```

Hermes asks before it installs the scanner package. Run the command in a terminal, and
restart Hermes if it was running. Use this rather than `pip install`, which would reach a
different Python than Hermes'.

**Upgrading from metaskill before MET-45:** metaskill used to install the scanner itself.
Hermes builds its Python environment from the enabled plugins and removes packages that
none of them declares, so after `hermes plugins update metaskill` the scanner is gone
until you install this add-on, and Safety is `partial` in the meantime.

Turn the add-on off with `hermes plugins disable metaskill-cisco`, or remove it with
`hermes plugins remove metaskill-cisco`. The scanner leaves Hermes' environment when
Hermes next rebuilds it, so restart Hermes afterwards.

## Notes

- The add-on has no code: installing it is what installs the scanner, and metaskill uses
  the scanner whenever it can import it.
- The version is pinned to the one metaskill's adapter is tested with. The scanner needs
  Python 3.11 to 3.14.
- At the `deep` safety tier, the scanner's LLM analyzer calls the model you configure in
  Hermes; at the default `static` tier it makes no model calls.
