"""metaskill-laya: optional Laya Experts PII model support for metaskill.

This plugin registers nothing. It exists so that installing it makes Hermes
install the ``laya`` package into the same Python environment as metaskill,
behind Hermes's own dependency-consent prompt. metaskill finds laya by
importing it when ``generalize`` is called with ``use_laya=true``.
"""


def register(ctx):
    """Nothing to register; the value is the declared dependency in plugin.yaml."""
