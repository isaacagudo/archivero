"""Configuration: built-in defaults, optionally overridden by a TOML file.

Example archivero.toml:

    [archivero]
    obsolete_years = 6
    long_video_min = 30

    [categories]
    code = ["cairo", "move"]   # extra extensions for a category
"""

import sys

DEFAULTS = {
    # Cleanup
    "obsolete_years": 5,          # software untouched for N years is considered obsolete
    "software_share": 0.8,        # minimum share of software (by bytes) in a folder
    "software_min_bytes": 1024 * 1024,
    # Video
    "long_video_min": 40,         # with ffprobe: minutes from which a video is "long"
    "long_video_gb": 1.0,         # without ffprobe: size from which a video is "long"
    # Duplicates
    "dupes_min_size": 1024,
    "dupes_workers": 4,
    # Archives and disk images
    "archive_stream_max_gb": 1.0,   # list index-less archives (tar.gz…) only below this size
    "archive_member_max_gb": 4.0,   # 'inspect' skips members larger than this
    "archive_match_share": 0.9,     # share of members found on disk to call it "already extracted"
    # Media index (archivero media)
    "media_workers": 4,             # parallel reads
    "media_thumb_px": 320,          # longest side of the WebP thumbnails
    "media_thumb_quality": 80,
    "media_max_read_mb": 512,       # images up to this size are read once into memory
    "media_tiny_px": 256,           # images this small with no camera in EXIF are flagged as non-personal
    # Music
    "music_folder_min_files": 3,
    "music_folder_share": 0.6,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    cfg["extra_extensions"] = {}
    if not path:
        return cfg
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            sys.exit("To use --config with Python < 3.11, install 'tomli' (pip install tomli).")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for k, v in data.get("archivero", {}).items():
        if k not in DEFAULTS:
            sys.exit("Unknown option in %s: %s" % (path, k))
        cfg[k] = type(DEFAULTS[k])(v)
    cfg["extra_extensions"] = {k: [e.lower().lstrip(".") for e in v]
                               for k, v in data.get("categories", {}).items()}
    return cfg
