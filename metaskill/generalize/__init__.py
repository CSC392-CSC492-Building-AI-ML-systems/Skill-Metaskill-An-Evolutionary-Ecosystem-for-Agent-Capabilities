"""Skill generalization. Only deterministic detection exists so far."""

from .detect import Category, Finding, ScanResult, detect, detect_file, redact, scan_skill
from .inventory import InventoryError, load_inventory

__all__ = [
    "Category",
    "Finding",
    "InventoryError",
    "ScanResult",
    "detect",
    "detect_file",
    "load_inventory",
    "redact",
    "scan_skill",
]
