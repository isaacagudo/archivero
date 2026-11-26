"""Detector registry. To add one, write a module with detect_child() or
detect_dir() (see base.py) and append it to these lists."""

from . import code, mail, music, prune, software
from .base import PRUNED, TAG, UNIT, Detection  # noqa: F401

# Applied to each subfolder before entering it; the first match wins.
CHILD_DETECTORS = [prune.detect_child, software.detect_child, mail.detect_child]

# Applied to each folder when entering it; every match is stored.
DIR_DETECTORS = [code.detect_dir, mail.detect_dir, music.detect_dir]

# Kinds that form an indivisible unit for the reorganization plan.
UNIT_KINDS = {"code_project", "thunderbird_profile", "browser_profile"}
