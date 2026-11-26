"""Folders not worth walking into."""

from .base import PRUNED, Detection

# lower-cased name -> (kind, regenerable)
PRUNE = {
    "node_modules": ("dependencies", True),
    "bower_components": ("dependencies", True),
    "jspm_packages": ("dependencies", True),
    ".git": ("vcs", False),
    ".svn": ("vcs", False),
    ".hg": ("vcs", False),
    "__pycache__": ("cache", True),
    ".pytest_cache": ("cache", True),
    ".mypy_cache": ("cache", True),
    ".gradle": ("cache", True),
    ".tox": ("cache", True),
    ".sass-cache": ("cache", True),
    ".venv": ("virtualenv", True),
    "venv": ("virtualenv", True),
    ".trash": ("trash", False),
    ".trashes": ("trash", False),
    "network trash folder": ("trash", False),
    "$recycle.bin": ("trash", False),
    "recycler": ("trash", False),
    "temporary items": ("system", False),
    ".temporaryitems": ("system", False),
    ".appledouble": ("system", False),
    ".spotlight-v100": ("system", False),
    ".fseventsd": ("system", False),
    ".documentrevisions-v100": ("system", False),
    "@eadir": ("system", False),
    "system volume information": ("system", False),
}


def detect_child(name, path):
    hit = PRUNE.get(name.lower())
    if hit:
        return Detection(hit[0], PRUNED, "not walked", hit[1])
    return None
