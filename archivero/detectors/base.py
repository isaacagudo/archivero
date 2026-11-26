"""Folder detector interface.

There are two kinds of detector. Both are cheap, pure functions (they never read
file contents):

* Child detector:  detect_child(name, path) -> Detection | None
  Called for every subfolder BEFORE entering it. Used to prune (node_modules,
  .git, trash, .app bundles) or to tag.

* Folder detector: detect_dir(path, entries, names_low, cfg) -> Detection | None
  Called when entering a folder, with its listing (os.DirEntry) and the
  lower-cased names. Used to recognize units (code project, mail profile) or
  to tag (music folder).
"""

from dataclasses import dataclass

UNIT = "unit"      # treated as a whole by the reorganization plan; still walked
PRUNED = "pruned"  # not walked; measured in bulk (files, bytes, dates)
TAG = "tag"        # annotation only


@dataclass
class Detection:
    kind: str
    mode: str
    note: str = ""
    regenerable: bool = False
