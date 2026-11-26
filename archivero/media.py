"""Video (ffprobe) and audio (mutagen) metadata. Both optional."""

import json
import shutil
import subprocess
import sys
from datetime import datetime

_FFPROBE = None
_MUTAGEN = None


def probe_video(path):
    """Return (duration_seconds, date) using ffprobe, or (None, None)."""
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = shutil.which("ffprobe") or False
        if not _FFPROBE:
            print("Warning: ffprobe not found; --probe-video is ignored.", file=sys.stderr)
    if not _FFPROBE:
        return (None, None)
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
             "-of", "json", path], capture_output=True, text=True, timeout=120).stdout
        fmt = json.loads(out or "{}").get("format", {})
    except Exception:
        return (None, None)
    dur = fmt.get("duration")
    try:
        dur = float(dur) if dur not in (None, "N/A") else None
    except ValueError:
        dur = None
    ct = (fmt.get("tags") or {}).get("creation_time")
    date = None
    if ct:
        try:
            date = datetime.strptime(ct[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            date = None
    return (dur, date)


def audio_tags(path):
    """Return (tags_dict, duration) using mutagen, or (None, None)."""
    global _MUTAGEN
    if _MUTAGEN is None:
        try:
            import mutagen
            _MUTAGEN = mutagen
        except ImportError:
            _MUTAGEN = False
            print("Warning: mutagen is not installed; --tags is ignored.", file=sys.stderr)
    if not _MUTAGEN:
        return (None, None)
    try:
        f = _MUTAGEN.File(path, easy=True)
    except Exception:
        return (None, None)
    if f is None:
        return (None, None)
    dur = getattr(getattr(f, "info", None), "length", None)
    tags = getattr(f, "tags", None) or {}

    def g(key):
        try:
            v = tags.get(key)
        except Exception:
            return None
        return str(v[0]).strip() if v else None

    out = {"artist": g("albumartist") or g("artist"), "album": g("album"),
           "title": g("title"), "date": g("date"), "genre": g("genre")}
    return ({k: v for k, v in out.items() if v}, dur)
