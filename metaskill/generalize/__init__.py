"""Skill generalization. Detection exists so far: the regex layer, plus extra
detectors from optional add-on plugins (extras.py), and the optional classifier for
findings left for review (classify.py)."""

# Not classify() itself: as a package attribute it would hide the classify module, so
# `metaskill.generalize.classify` must stay the module. Import it from there.
from .classify import ClassificationRun, Classifier, Label, LlmClassifier
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
    "ClassificationRun",
    "Classifier",
    "Finding",
    "InventoryError",
    "Label",
    "LlmClassifier",
    "ScanResult",
    "detect",
    "detect_file",
    "detect_file_with_claims",
    "generalize_skill",
    "load_inventory",
    "redact",
    "scan_skill",
]
