## Summary

<!-- What changed and why. Link the ticket. -->

## Checklist

- [ ] `python -m pytest` passes (CI runs it on Linux and Windows).
- [ ] Docs under `docs/` match the change.
- [ ] **Windows + Hermes** (only if this touches `metaskill/evaluate/hermes_agent.py`, `hermes_child.py`, `winjob.py` or `sandbox.py`): on Windows with Docker Desktop running and Hermes 0.23 or later, `METASKILL_HERMES_IT=1 python -m pytest tests/test_hermes_agent.py` passes, with no test skipped for a Microsoft Store Python. CI cannot run these: GitHub's Windows runners have no Linux containers ([hermes_agent.md](../docs/evaluate/hermes_agent.md) 5).
