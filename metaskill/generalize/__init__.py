"""Skill generalization. Only deterministic detection exists so far."""

from .detect import (
    Action,
    Category,
    Finding,
    ScanResult,
    detect,
    detect_file,
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
    "load_inventory",
    "redact",
    "scan_skill",
    "generalize_skill"
]
