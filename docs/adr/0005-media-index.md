# 0005. Media index keyed by content hash, with thumbnails

- Status: Accepted (implemented in v0.2, milestone M0)
- Date: 2026-11-28

## Context

memento (the photo and video catalog) needs, for every unique picture, its metadata, a thumbnail and every place
the same content lives. Reading photos over the network is the slow part, so it should happen once, during
archivero's sweep, and serve every later tool.

## Decision

- New command `archivero media` (and `scan --media`). For each photo and video it reads the file **once** and from
  that read computes the BLAKE2b hash, normalized metadata (capture date and source, offset, GPS, camera make,
  model and serial, lens, size, orientation, duration, codecs), a **320 px WebP thumbnail** stored in a
  content-addressed cache next to the database (`<db>.media/ab/cd/<hash>.webp`), and a 64-bit **dHash**.
- New table **`media`**, one row per unique hash. Every copy is found through `files.full_hash` and
  `members.full_hash`; identical copies are processed only once.
- Resumable; skips content already indexed. Schema version bump, since memento depends on these tables.

## Consequences

- About 8 h per 500 GB over NFS at 15–20 MB/s; thumbnails take about 1.5 GB per 100,000 photos.
- Large previews are not stored; memento renders them on demand.
- Milestone M0 of memento; see memento's [DESIGN.md](https://github.com/isaacagudo/memento/blob/main/docs/DESIGN.md).
