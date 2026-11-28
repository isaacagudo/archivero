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
- **`errors`**: paths that could not be read.
- **`meta`**: inventory root, the configuration used, and the schema version.

### Migrations

`db.SCHEMA_VERSION` goes up with every schema change. `open_db()` will run the pending
migrations in order: add a `_migrate_N_to_N+1(con)` function for each step. A database
created by a newer version is rejected.

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

## Performance

- The walk is iterative (no recursion) with `os.scandir`, which reuses the
  directory listing.
- Over NFS, the latency of every `stat` dominates. A million files take hours, not
  days. Parallel scanning by subtree is on the roadmap.
- `dupes` only reads files that share their size with another file, and reads in
  full only those whose quick hashes also match.
- `Analysis` keeps one tuple per file in memory, which is fine for several million
  files. For more, the roadmap moves the aggregates into SQL.
