# 0002. Duplicates by size, quick hash, then full BLAKE2b

- Status: Accepted
- Date: 2026-11-28

## Context

Backups read over the network (NFS at 15–20 MB/s) make reading every byte of every file expensive. Most files have
a unique size, and most same-size files differ in their first or last bytes.

## Decision

- Only files that share a size (and are above `dupes_min_size`) are hashed.
- First a **quick hash**: BLAKE2b of the first and last 64 KB. Only quick-hash collisions get a **full hash**
  (BLAKE2b of the whole file).
- Hashing runs in a thread pool (`dupes_workers`) and is resumable.
- BLAKE2b is the content identity used everywhere else (archive members, the media index).

## Consequences

- Full content is read only for likely duplicates.
- The known-file filter needs SHA-1 or MD5 as well ([0007](0007-known-file-filter.md)); that is a separate column,
  computed only when the filter is on.
