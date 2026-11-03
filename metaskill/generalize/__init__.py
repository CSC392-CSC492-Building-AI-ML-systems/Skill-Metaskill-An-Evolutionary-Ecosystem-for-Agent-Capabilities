"""Skill generalization. Detection exists so far: the regex layer, plus extra
detectors from optional add-on plugins (extras.py)."""

from .detect import (
    Action,
    Category,
    Finding,
    ScanResult,
    detect,
    detect_file,
    detect_file_with_claims,
    redact,
    scan_skill,
)
from .inventory import InventoryError, load_inventory
from .pipeline import generalize_skill

__all__ = [
    "Action",
    "Category",
    "Finding",
    "InventoryError",
    "ScanResult",
    "detect",
    "detect_file",
    "detect_file_with_claims",
    "generalize_skill",
    "load_inventory",
    "redact",
    "scan_skill",
]
