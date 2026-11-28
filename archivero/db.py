"""SQLite inventory database (versioned schema)."""

import sqlite3
import sys

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- One row per inventoried file.
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    dir         TEXT NOT NULL,
    name        TEXT NOT NULL,
    ext         TEXT,
    size        INTEGER,
    mtime       REAL,
    category    TEXT,          -- image, video_short, audio, pdf, code, email, ...
    subtype     TEXT,          -- e.g. mbox, thunderbird_index, pst
    method      TEXT,          -- how it was decided (video: duration | size)
    duration    REAL,
    date_best   TEXT,          -- 'YYYY-MM-DD HH:MM:SS'
    date_source TEXT,          -- exif | video_metadata | filename | mtime
    extra       TEXT,          -- JSON with type-specific metadata (audio tags, ...)
    quick_hash  TEXT,
    full_hash   TEXT,
    scanned_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_dir  ON files(dir);
CREATE INDEX IF NOT EXISTS idx_files_full ON files(full_hash);
CREATE INDEX IF NOT EXISTS idx_files_cat  ON files(category);

-- Folders recognized by detectors.
--   mode = 'unit'   : treated as a whole (code project, mail profile); still walked.
--   mode = 'pruned' : not walked (node_modules, .git, trash, .app); measured in bulk.
--   mode = 'tag'    : annotation only (music folder, Apple Mail mailbox).
CREATE TABLE IF NOT EXISTS detections (
    path        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    note        TEXT,
    regenerable INTEGER DEFAULT 0,
    files       INTEGER,
    bytes       INTEGER,
    newest      REAL,
    oldest      REAL,
    PRIMARY KEY (path, kind)
);

CREATE TABLE IF NOT EXISTS errors (path TEXT, error TEXT, at REAL);

-- Archives and disk images found among the files (see archives.py).
--   status: listed | inspected | deferred (no index, too big) | unsupported | error
CREATE TABLE IF NOT EXISTS containers (
    path         TEXT PRIMARY KEY,
    format       TEXT,
    backend      TEXT,
    status       TEXT,
    n_members    INTEGER,
    n_dirs       INTEGER,
    total_size   INTEGER,      -- uncompressed
    note         TEXT,
    listed_at    REAL,
    inspected_at REAL
);

-- Files inside a container, read from its index (and hashed by 'archives inspect').
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY,
    container   TEXT NOT NULL,
    inner_path  TEXT NOT NULL,
    name        TEXT,
    ext         TEXT,
    size        INTEGER,
    mtime       REAL,
    crc32       TEXT,
    is_dir      INTEGER DEFAULT 0,
    encrypted   INTEGER DEFAULT 0,
    category    TEXT,
    subtype     TEXT,
    date_best   TEXT,
    full_hash   TEXT,
    UNIQUE (container, inner_path)
);
CREATE INDEX IF NOT EXISTS idx_members_container ON members(container);
CREATE INDEX IF NOT EXISTS idx_members_size ON members(size);
CREATE INDEX IF NOT EXISTS idx_members_full ON members(full_hash);

-- Media index (ADR 0005): one row per unique photo or video, keyed by its content hash
-- (the same BLAKE2b as files.full_hash and members.full_hash). Built by 'archivero media'.
--   status : ok | partial (something could not be read yet, see 'missing') | error
--   date_* : only dates stored inside the content; per-copy dates stay in files/members
CREATE TABLE IF NOT EXISTS media (
    hash          TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,   -- image | raw | video
    format        TEXT,            -- JPEG, PNG, HEIF, MPEG-4…
    size          INTEGER,
    width         INTEGER,         -- as displayed (orientation applied)
    height        INTEGER,
    orientation   INTEGER,         -- EXIF orientation 1-8, or video rotation in degrees
    duration      REAL,
    video_codec   TEXT,
    audio_codec   TEXT,
    date_taken    TEXT,            -- 'YYYY-MM-DD HH:MM:SS', as recorded (local time unless date_offset says)
    date_source   TEXT,            -- exif_original | exif_digitized | exif_datetime | quicktime | video_utc
    date_offset   TEXT,            -- '+02:00' when recorded
    gps_lat       REAL,
    gps_lon       REAL,
    gps_alt       REAL,
    make          TEXT,
    model         TEXT,
    serial        TEXT,
    lens          TEXT,
    software      TEXT,
    dhash         TEXT,            -- 64-bit difference hash, 16 hex digits
    thumb         TEXT,            -- WebP path relative to the thumbnail cache (<db>.media/)
    nonpersonal   INTEGER DEFAULT 0,  -- ADR 0006: icon, wallpaper, cache… (a flag, never a deletion)
    nonpersonal_why TEXT,
    status        TEXT,
    missing       TEXT,            -- partial rows: ffmpeg | heif | raw | archive_video
    error         TEXT,
    indexed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_media_date ON media(date_taken);
CREATE INDEX IF NOT EXISTS idx_media_dhash ON media(dhash);

-- Known-file filter (ADR 0007): SHA-1 of software-like files, looked up in public hash sets
-- (CIRCL hashlookup Bloom filter, NSRL RDSv3, optionally the CIRCL API). Built by 'archivero known'.
--   status : known | unknown (not in any set checked so far) | NULL (not looked up yet)
CREATE TABLE IF NOT EXISTS known (
    hash       TEXT PRIMARY KEY,   -- BLAKE2b-160, the same as files.full_hash
    sha1       TEXT NOT NULL,      -- upper-case hex, as the hash sets store it
    status     TEXT,
    source     TEXT,               -- set that knows it: bloom:<name> | nsrl:<name> | circl-api
    name       TEXT,               -- file name given by the set, when it gives one
    checked    TEXT,               -- sets it was looked up in, comma-separated
    hashed_at  REAL,
    checked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_known_sha1 ON known(sha1);

-- Every copy of every indexed asset: files on disk and members of archives or disk images.
CREATE VIEW IF NOT EXISTS media_instances AS
    SELECT m.hash, 'file' AS origin, f.path, NULL AS container, NULL AS inner_path,
           f.size, f.mtime, f.date_best, f.date_source
      FROM media m JOIN files f ON f.full_hash = m.hash
    UNION ALL
    SELECT m.hash, 'member', mb.container || '/' || mb.inner_path, mb.container, mb.inner_path,
           mb.size, mb.mtime, mb.date_best, NULL
      FROM media m JOIN members mb ON mb.full_hash = m.hash;
"""


def open_db(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    v = get_meta(con, "schema_version")
    if v is None:
        set_meta(con, "schema_version", SCHEMA_VERSION)
        con.commit()
        return con
    v = int(v)
    if v > SCHEMA_VERSION:
        sys.exit("Database %s was created by a newer archivero (schema %s)." % (path, v))
    while v < SCHEMA_VERSION:
        MIGRATIONS[v](con)
        v += 1
        set_meta(con, "schema_version", v)
        con.commit()
    return con


def _migrate_1_to_2(con):
    """v0.2 M0: 'media' table and 'media_instances' view (created by SCHEMA above)."""


# MIGRATIONS[n] upgrades a database from schema n to n + 1. SCHEMA only uses
# CREATE … IF NOT EXISTS, so new tables appear on their own; a migration handles
# changes to existing tables (ALTER TABLE, backfills).
MIGRATIONS = {1: _migrate_1_to_2}


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def require_root(con):
    root = get_meta(con, "root")
    if not root:
        sys.exit("This database has no inventory yet. Run 'scan' first.")
    return root
