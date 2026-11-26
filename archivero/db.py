"""SQLite inventory database (versioned schema)."""

import sqlite3
import sys

SCHEMA_VERSION = 1

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
    elif int(v) > SCHEMA_VERSION:
        sys.exit("Database %s was created by a newer archivero (schema %s)." % (path, v))
    # Migrations go here when SCHEMA_VERSION is bumped (see docs/ARCHITECTURE.md).
    return con


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
