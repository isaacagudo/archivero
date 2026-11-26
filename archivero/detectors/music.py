"""Music folders (albums or collections of tracks)."""

import os

from ..categories import CATEGORY_EXT
from .base import TAG, Detection

AUDIO_EXT = set(CATEGORY_EXT["audio"].split())


def detect_dir(path, entries, names_low, cfg):
    n_files = n_audio = 0
    for e in entries:
        try:
            if not e.is_file(follow_symlinks=False):
                continue
        except OSError:
            continue
        n_files += 1
        if os.path.splitext(e.name)[1].lower().lstrip(".") in AUDIO_EXT:
            n_audio += 1
    if n_audio >= cfg["music_folder_min_files"] and n_audio >= cfg["music_folder_share"] * n_files:
        return Detection("music_folder", TAG, "%d tracks" % n_audio)
    return None
