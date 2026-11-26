"""Email: Thunderbird (and Firefox) profiles, Apple Mail mailboxes."""

from .base import TAG, UNIT, Detection

THUNDERBIRD_HINTS = {"mail", "imapmail", "news", "abook.sqlite", "abook.mab", "history.mab",
                     "global-messages-db.sqlite", "msgfilterrules.dat", "panacea.dat",
                     "virtualfolders.dat", "mailviews.dat"}
FIREFOX_HINTS = {"places.sqlite", "cookies.sqlite", "formhistory.sqlite", "sessionstore.jsonlz4"}


def detect_dir(path, entries, names_low, cfg):
    if "prefs.js" in names_low:
        tb = names_low & THUNDERBIRD_HINTS
        if tb:
            return Detection("thunderbird_profile", UNIT, ", ".join(sorted(tb)))
        ff = names_low & FIREFOX_HINTS
        if ff:
            return Detection("browser_profile", UNIT, "Firefox/Mozilla: " + ", ".join(sorted(ff)))
    if "profiles.ini" in names_low and ("profiles" in names_low or any(
            n.endswith(".default") or ".default-" in n for n in names_low)):
        return Detection("mozilla_dir", TAG, "contains profiles.ini")
    return None


def detect_child(name, path):
    low = name.lower()
    if low.endswith(".mbox"):
        # Apple Mail stores each mailbox as a *.mbox folder with .emlx files inside.
        return Detection("apple_mail_mailbox", TAG, ".mbox folder")
    if low.endswith(".sbd"):
        return Detection("thunderbird_subfolders", TAG, ".sbd folder")
    return None
