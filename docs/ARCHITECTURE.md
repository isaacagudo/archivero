# Architecture

## Principles

1. **Read-only on the source.** Nothing deletes or moves. Destructive actions
   (quarantine, deletion) will be separate commands, logged and undoable.
2. **Separate data collection from analysis.** `scan` and `dupes` are slow and touch
   the disk; what they collect goes into SQLite. `report` and `plan` only read the
   database, so they can be re-run with other thresholds in seconds.
3. **Everything is resumable.** A slow NAS over the network can take hours. If the
   process stops, running the same command again picks up where it left off.
4. **Cheap metadata before content.** Name, size and dates come first. Reading
   content (EXIF, ID3 tags, ffprobe, hashes) is optional or limited to candidates.
5. **Extensible through detectors.** The rules that give a folder its meaning are
   small functions registered in a list.

## Flow

```
           ┌────────┐   ┌────────┐            ┌──────────┐   ┌──────────────────────────┐
 source ──▶│  scan  │──▶│ dupes  │──▶ SQLite ─▶│ Analysis │──▶│ report (HTML + CSV)      │
           └────────┘   └────────┘            └──────────┘   │ plan (editable CSV)      │
              │ detectors                                     └────────────┬─────────────┘
              ▼                                                            ▼
        detections (units,                                         apply (copy)
        pruned, tags)
```

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | Commands and arguments |
| `config.py` | Defaults and TOML loading |
| `db.py` | Versioned SQLite schema (`meta.schema_version`) |
| `categories.py` | Extension → category; mbox recognition; what counts as software |
| `dates.py` | Best date: EXIF, file name, mtime; rejects impossible dates |
| `media.py` | ffprobe (video) and mutagen (audio), both optional |
| `detectors/` | Rules that recognize folders (see below) |
| `scanner.py` | Iterative walk with `os.scandir`; applies detectors and stores files |
| `dupes.py` | Size → quick hash (first and last 64 KB) → full BLAKE2b, in parallel |
| `archives.py` | List and hash archive / disk image members without extracting (zip, tar, libarchive and 7-Zip backends) |
| `layouts.py` | OS layout knowledge as data: scored markers, zone rules, provenance clues |
| `zones.py` | Rebuilds the folder tree, detects drives and profiles (partial copies too), assigns zones, describes sources |
| `analysis.py` | Per-folder aggregates, units, zones and sources, duplicates, cleanup proposals, folder labels |
| `report.py` | Self-contained HTML report and CSV exports |
| `plan.py` | Reorganization proposal and `apply` (copy) |
| `media_index.py` | `archivero media`: the media index (metadata, thumbnails, dHash, non-personal flags) |
| `known.py` | `archivero known`: SHA-1 of software-like files, looked up in the CIRCL Bloom filter, NSRL or the CIRCL API |
| `osversion.py` | Parsers for the files that state an OS version (registry hive, `SystemVersion.plist`, `os-release`…) |

## Data model

- **`files`**: one row per file. It holds the path, size, `mtime`, the category and
  subtype (`mbox`, `pst`…), the chosen date and where it came from, the hashes, and
  an `extra` JSON column for type-specific metadata (audio tags today; mail
  summaries or camera model tomorrow).
- **`detections`**: recognized folders, keyed by `(path, kind)`, with a `mode`:
  - `unit`: an indivisible whole in the plan (code project, Thunderbird profile).
    It is still walked, so its content is inventoried.
  - `pruned`: not walked (`node_modules`, `.git`, trash, `.app`). It is measured in
    bulk: number of files, bytes, and newest and oldest dates.
  - `tag`: annotation only (music folder, Apple Mail mailbox).
- **`containers`**: every archive or disk image found among the files, with its
  format, the backend that read it, a status (`listed`, `inspected`, `deferred`,
  `unsupported`, `error`), its number of members and uncompressed size.
- **`members`**: the files inside each container, read from its index (name, size,
  date, CRC32 when the format stores it) and, after `archives inspect`, their
  BLAKE2b hash. They are classified like any other file, but are not part of
  `files`, so they never end up in the reorganization plan on their own.
- **`media`**: one row per unique photo or video, keyed by content hash (the same
  BLAKE2b as `files.full_hash` and `members.full_hash`). See [Media index](#media-index-media_indexpy).
  The **`media_instances`** view lists every copy of each one: files on disk and archive members.
- **`known`**: one row per hashed content (keyed by the same BLAKE2b), with its SHA-1 and what the
  known-file sets said about it. See [Known-file filter](#known-file-filter-knownpy).
- **`errors`**: paths that could not be read.
- **`meta`**: inventory root, the configuration used, and the schema version.

### Migrations

`db.SCHEMA_VERSION` goes up with every schema change; memento depends on it (`files`,
`members`, `media`). `open_db()` runs the pending migrations in order: add a
`_migrate_N_to_N+1(con)` function to `db.MIGRATIONS` for each step. New tables need no
code, since the schema only uses `CREATE … IF NOT EXISTS`. A database created by a newer
version is rejected.

| Version | Change |
|---|---|
| 1 | v0.1–v0.2 tables |
| 2 | `media` table and `media_instances` view (milestone M0); later also the `known` table, a new table only, so the version did not change |

## Detectors

There are two signatures. Neither reads file contents:

```python
def detect_child(name, path) -> Detection | None                    # before entering a subfolder
def detect_dir(path, entries, names_low, cfg) -> Detection | None   # when entering a folder
```

To add one, write the function in `detectors/` and register it in
`detectors/__init__.py`. If it represents an indivisible unit, add its `kind` to
`UNIT_KINDS` and its destination folder to `plan.UNIT_DEST`.

Current detectors:

| Detector | Recognizes | Mode |
|---|---|---|
| `prune` | `node_modules`, `.git`, `__pycache__`, `.venv`, trash folders, system folders | pruned |
| `software` | `.app`, `.framework`, `.pkg` bundles… | pruned |
| `code` | Project roots (`package.json`, `.git`, `pom.xml`, `*.xcodeproj`…) | unit |
| `mail` | Thunderbird profile (`prefs.js` plus `Mail/`, `ImapMail/`, `abook.*`…), Firefox profile, Apple Mail `.mbox` folders, `.sbd` folders | unit / tag |
| `music` | Folder with 3 or more tracks that make up at least 60% of its files | tag |

Thunderbird mbox files have no extension. They are recognized by a same-named `.msf`
file next to them or, inside a profile, by their name (`Inbox`, `Sent`, and
localized names such as `Enviados`).

## Cleanup rules (`analysis.py`)

| Type | Rule |
|---|---|
| `obsolete_software` | A folder where at least 80% of the bytes are software (executables, disk images, bundles, archives named like installers) and nothing changed in `obsolete_years`. Only the top-most qualifying folder is proposed, never inside a project |
| `old_installer` | A stand-alone software file of 1 MB or more, old, outside the folders above |
| `regenerable` | `node_modules`, caches and virtual environments |
| `trash`, `system` | Local or network trash folders, internal OS folders |
| `duplicate` | Identical copies. The copy kept is the one inside a unit, or the one that does not look like a backup, or the oldest, or the one with the shortest path |
| `system_junk` | `.DS_Store`, `._*`, `Thumbs.db`… |
| `os_files`, `installed_programs`, `cache_files` | Top-most folder of each `system`, `applications` or `cache` zone found by the layout engine. If personal-looking files sit inside, the reason says how many, so they can be reviewed |
| `known_software` | After `archivero known`: the top-most folder where files known to the hash sets hold at least `software_share` of the bytes (personal-looking files inside are counted in the reason), and single known files of `software_min_bytes` or more. Known files also count as software in folder totals |

## Archives and disk images (`archives.py`, `analysis.py`)

Two levels, so the cost is only paid where it pays off:

1. **`archives list`** reads only the index: the zip central directory (which
   includes CRC32), the 7z header, the ISO directory, or the `7z l -slt` listing of a
   disk image. Compressed tarballs have no index, so they are listed only below
   `archive_stream_max_gb`; above it they are marked `deferred`.
2. **`archives inspect`** streams every member through BLAKE2b in memory: nothing is
   written to disk, and members above `archive_member_max_gb` are skipped. It then
   hashes the files on disk that share a size with a hashed member, so matches are
   exact.

Matching an archive with a folder on disk works with name and size, which is enough to
*suggest*, and with hashes, which is required to *propose deleting*. For each member
found on disk, the folder corresponding to the archive root is computed by going up
as many levels as the member is deep inside the archive. The folder with most votes
wins, so a renamed folder is still found. The suggestions are:

| Suggestion | Rule |
|---|---|
| `already_extracted` | ≥ `archive_match_share` (90%) of its files found on disk. Proposed for cleanup (`extracted_archive`) only when **confirmed by hash** |
| `inspect` | Personal files (photos, documents, mail, audio…) not found anywhere on disk |
| `software_image` | ≥ 60% of its bytes are software |
| `partially_extracted` | 30–90% found on disk |
| `deferred` / `unsupported` / `error` | Could not be listed: too big without an index, missing backend, or corrupt |

Archives with personal content are never treated as software, so an old `.dmg` or
`.zip` holding photos is not proposed as an "old installer".

## Media index (`media_index.py`)

`archivero media` (or `scan --media`) builds the index memento reads ([ADR 0005](adr/0005-media-index.md)).
It needs Pillow; ffmpeg and `pillow-heif` are optional.

- **Read once.** A photo is read into memory once (up to `media_max_read_mb`), and those bytes
  give the hash, the metadata, the thumbnail and the dHash. A video is hashed in one streaming
  pass; then ffprobe reads its header and ffmpeg decodes one frame.
- **Once per content.** Copies whose hash is already known (from `dupes`, `archives inspect`
  or an earlier run) are not read again. Copies not hashed yet must be read to be hashed, but
  are not decoded twice. The hash is saved to `files.full_hash` / `members.full_hash`.
- **Resumable.** A run picks up whatever has no `media` row yet. Rows are `ok`, `error` (not
  retried; `--force` re-indexes everything) or `partial`, with what was `missing`:
  `ffmpeg`, `heif` (both retried once the tool is installed), `archive_video` (a video seen
  only inside an archive: completed when a copy on disk turns up) or `raw` (no preview found).
- **Metadata** comes only from the content: EXIF `DateTimeOriginal` (then `DateTimeDigitized`,
  then `DateTime`) with its offset, GPS, make, model, body serial, lens, software and
  orientation; for videos, codecs, duration, rotation, the QuickTime local creation date (else
  `creation_time`, in UTC), ISO 6709 location and the Apple/Android make and model. Dates per
  copy (file name, `mtime`) stay in `files` and `members`; memento weighs them all.
- **Thumbnails**: WebP, 320 px on the longest side, orientation applied, stored by hash in
  `<db>.media/ab/cd/<hash>.webp` (relative path in `media.thumb`). JPEGs are decoded at a reduced
  scale (`draft`). RAW files use their largest embedded JPEG preview; their EXIF is read from the
  TIFF structure when there is one. HEIC/HEIF needs `pillow-heif`.
- **dHash**: 64-bit difference hash (9×8 greyscale), 16 hex digits, from the oriented image or
  the video frame. Near duplicates are within a few bits of each other.
- **Long videos** (`video_long`, usually films) are left out unless `--long-videos`.
- **Archives**: with `--archives`, pictures inside listed archives and disk images are streamed
  in memory through the archive backends, as `archives inspect` does. Nothing is extracted.

### Non-personal flags ([ADR 0006](adr/0006-non-personal-images.md))

Recomputed at the end of every run, from the content and from **every** copy:

| Reason | Rule |
|---|---|
| `zone:system`, `zone:applications`, `zone:cache` | The copy sits in that zone (`zones.py`). `trash` and `user_appdata` are not reasons |
| `icon_folder`, `wallpaper_or_sample_folder`, `cache_folder` | A folder on the way is named like one (`icons`, `Sample Pictures`, `Wallpaper`, `Temporary Internet Files`…) |
| `code_project`, `browser_profile` | The copy is inside one of those units |
| `tiny` | At most `media_tiny_px` (256) on its longest side, with no camera in EXIF |
| `stock_name` | Named like a picture shipped with Windows or macOS (`Koala.jpg`, `Bliss.jpg`…), with no camera in EXIF |
| `known_file` | Its hash is in a known-file set (`archivero known --images`, [ADR 0007](adr/0007-known-file-filter.md)) |

An asset is flagged when a content reason applies (`tiny`, `stock_name`, `known_file`) or when **all** of its
copies have a place reason: a photo that is in a browser cache and also in `Pictures` stays
personal. The flag (`nonpersonal`, with the reasons in `nonpersonal_why`) is data: memento hides
flagged assets by default, and nothing is deleted.

## Exact OS versions (`osversion.py`, [ADR 0008](adr/0008-exact-os-versions.md))

`scan` reads the file where an OS records its version when it meets one at the expected path
(`layouts.VERSION_FILES`), and stores what it says in `files.extra` as `os_version`:

| OS | File (relative to the drive) | What it gives |
|---|---|---|
| Windows | `Windows/System32/config/SOFTWARE` | Registry hive, `Microsoft\Windows NT\CurrentVersion`: product, service pack or display version (`22H2`), build |
| Windows 2000/XP | `Windows/System32/prodspec.ini` | Product (`Windows XP Professional`), when the hive was not copied |
| macOS | `System/Library/CoreServices/SystemVersion.plist` | Product, version and build; the release name is added (`10.6` → Snow Leopard) |
| Linux | `etc/os-release`, `usr/lib/os-release`, `etc/lsb-release`, `etc/*-release`, `etc/debian_version` | Distribution and version |

The hive reader is a minimal read-only regf parser that seeks along the path to one key, so it reads a few
kilobytes; text files are read up to 64 KB. The analysis attaches the version to the drive the file belongs to
and it replaces the era guess in the description.

## Known-file filter (`known.py`)

`archivero known` ([ADR 0007](adr/0007-known-file-filter.md)) recognizes OS and software files by content, so a
program folder copied out of its drive, or a loose installer, is recognized too.

1. **Candidates**, by category and zone: software (executables, libraries, disk images, archives named like
   installers) anywhere; code, configuration, unknown types and files with no extension outside `user_data`;
   pictures outside `user_data` only with `--images`. Never documents, photos, video, audio, mail, keys or
   databases, nor `pagefile.sys`/`hiberfil.sys`, nor files above `known_max_gb` (0.5).
2. **Hashing**: one read gives BLAKE2b (saved to `files.full_hash` when missing) and SHA-1. A content already in
   `known` is not read again, so the command is resumable.
3. **Lookups** of every content not known yet, against each set given:
   - `--bloom FILE`: the CIRCL hashlookup Bloom filter (DCSO format: a 48-byte header, then the bit array; 64-bit
     FNV-1 of the upper-case SHA-1). It is checked in place with `mmap`; nothing is loaded or sent.
   - `--nsrl FILE`: an NSRL RDSv3 SQLite database, opened read-only; one pass over its `FILE` view per batch.
   - `--online`: the CIRCL bulk API, only for the SHA-1s of executables that no offline set knows and that were
     never sent before.
4. The sets used are kept in `meta.known_sets`; the media index's non-personal flags are recomputed.

`known_bloom` and `known_nsrl` in the configuration name default sets. The report gains a "Known files" section
(counts, sets, and unknown executables in system or program folders) and `files.csv` a `known` column.

## Performance

- The walk is iterative (no recursion) with `os.scandir`, which reuses the
  directory listing.
- Over NFS, the latency of every `stat` dominates. A million files take hours, not
  days. Parallel scanning by subtree is on the roadmap.
- `dupes` only reads files that share their size with another file, and reads in
  full only those whose quick hashes also match.
- `Analysis` keeps one tuple per file in memory, which is fine for several million
  files. For more, the roadmap moves the aggregates into SQL.
