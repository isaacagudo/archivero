"""Software packages that are folders (macOS .app, .framework, ...)."""

from .base import PRUNED, Detection

BUNDLE_SUFFIXES = (".app", ".framework", ".bundle", ".plugin", ".kext", ".prefpane", ".mpkg", ".pkg")


def detect_child(name, path):
    low = name.lower()
    if low.endswith(BUNDLE_SUFFIXES):
        return Detection("software_bundle", PRUNED, "%s bundle" % low.rsplit(".", 1)[-1])
    return None
