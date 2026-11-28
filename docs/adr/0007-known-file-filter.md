# 0007. Known-file filter from public hash sets

- Status: Accepted (implemented in v0.2)
- Date: 2026-11-28

## Context

Zones recognize OS and program files by location. Files copied out of their place (a `Program Files` folder
inside `Backup/old`) or loose installers are only recognized by content. Forensic hash sets list millions of known
OS and software files.

## Decision

- Optional filter, `archivero known`, that marks files whose hash is in a known-file set: the CIRCL hashlookup
  Bloom filter (about 1 GB, no network; checked in place with mmap) or NSRL RDSv3 databases (opened read-only); the
  online CIRCL API only as an explicit option (`--online`), and then only for executables never sent before.
- Needs SHA-1 of candidate files, computed only by this command: software files anywhere, and other non-personal
  files (code, configuration, unknown types, files with no extension) outside user folders. Documents, photos,
  mail, audio, keys and databases are never hashed for it; pictures outside user folders only with `--images`, and
  only against the offline sets. Files above `known_max_gb` and memory files (`pagefile.sys`, `hiberfil.sys`) are
  left out.
- Results live in a new `known` table keyed by content (the same BLAKE2b as `files.full_hash`), so hashing is done
  once and resumable, and new sets are checked later without reading the files again. The schema version stays
  at 2: the tables memento reads (`files`, `members`, `media`) do not change, and older readers ignore the new table.
- Known files feed cleanup proposals (`known_software`: top-most folders where known files hold at least
  `software_share` of the bytes, and single known files), count as software in folder totals, never count as
  personal, and add a `known_file` reason to the media index's non-personal flag (ADR 0006). Executables in
  system or program folders that no set knows are listed for review.

## Consequences

- Works offline by default; nothing is sent unless the user enables the API, and then only SHA-1s of executables.
- A Bloom filter can answer "known" for a file it does not hold (CIRCL's rate is in the file header, shown in the
  report); a false "known" only makes a file a cleanup *proposal*, never a deletion.
- Archive members are not looked up yet: they would need a second streaming pass to compute SHA-1.
