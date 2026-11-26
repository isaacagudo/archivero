"""Picking the most reliable date for a file."""

import re
import sys
from datetime import datetime

FILENAME_DATE_RE = re.compile(
    r"(?<!\d)(19[89]\d|20[0-4]\d)[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)")


def ts_to_str(ts):
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def plausible(dt_str):
    """Reject absurd dates (zeroed EXIF, unset clocks, future)."""
    if not dt_str:
        return False
    try:
        year = int(dt_str[:4])
    except ValueError:
        return False
    return 1980 <= year <= datetime.now().year + 1


def from_filename(name):
    m = FILENAME_DATE_RE.search(name)
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError:
        return None
    return "%s-%s-%s 00:00:00" % (y, mo, d)


_PIL = None


def exif_date(path):
    """EXIF capture date (requires Pillow). Returns None if unavailable."""
    global _PIL
    if _PIL is None:
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            _PIL = Image
        except ImportError:
            _PIL = False
            print("Warning: Pillow is not installed; --exif is ignored.", file=sys.stderr)
    if not _PIL:
        return None
    try:
        with _PIL.open(path) as im:
            ex = im.getexif()
            val = None
            try:
                val = ex.get_ifd(0x8769).get(36867)  # DateTimeOriginal
            except Exception:
                pass
            val = val or ex.get(36867) or ex.get(306)  # DateTime
    except Exception:
        return None
    if not val:
        return None
    val = str(val).strip().replace("\x00", "")
    try:
        return datetime.strptime(val[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def best_date(candidates):
    """candidates: ordered list of (date, source). Returns the first plausible one."""
    for value, source in candidates:
        if plausible(value):
            return value, source
    return None, None
