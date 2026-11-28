# 0003. Archives and disk images inspected without extracting

- Status: Accepted
- Date: 2026-11-28

## Context

Backups contain zips, tarballs, 7z files and disk images, sometimes next to their own extracted copy. Extracting
them to look inside would need as much free space as the archives themselves.

## Decision

- **List first**: read only the index (names, sizes, dates, CRC) through zipfile/tarfile, libarchive or the 7-Zip
  CLI. Archives without an index above `archive_stream_max_gb` are deferred.
- **Inspect on request** (`archives inspect --suggested | --all`): stream members through BLAKE2b, never to disk.
- Members are matched with files on disk by name and size to **suggest**, and by hash to **propose cleanup**: an
  archive is reported as already extracted only when its content is confirmed by hash.
- Members get the same zone and category analysis as files, so a zipped Windows drive is recognized as such.

## Consequences

- No temporary space is needed, even for multi-GB images.
- Name+size look-alikes are never proposed for cleanup.
