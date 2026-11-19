"""metaskill-cisco: an optional add-on that installs Cisco's skill scanner for metaskill.

The add-on carries no code of its own. Its plugin.yaml declares the scanner, so Hermes
installs it into its own environment when the add-on is installed. metaskill's safety
adapter (metaskill/evaluate/safety/cisco.py) uses the scanner whenever it can be imported;
without it, evaluate scores safety with metaskill's own checks only, as `partial`.
"""


def register(ctx):
    """Nothing to register: installing the add-on is what installs the scanner."""
