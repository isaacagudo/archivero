# Architecture decision records

One file per decision: the context, what was decided and what follows. A record is not rewritten when the decision
changes; a new record supersedes it and the old one's status points to the new one. Copy
[0000-template.md](0000-template.md) to start a new one. *Proposed* means decided but not implemented yet.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-read-only-on-the-source.md) | Read-only on the source | Accepted |
| [0002](0002-two-stage-hashing.md) | Duplicates by size, quick hash, then full BLAKE2b | Accepted |
| [0003](0003-archives-without-extracting.md) | Archives and disk images inspected without extracting | Accepted |
| [0004](0004-zones-and-partial-copies.md) | Zones from OS layouts, tolerant of partial copies | Accepted |
| [0005](0005-media-index.md) | Media index keyed by content hash, with thumbnails | Accepted |
| [0006](0006-non-personal-images.md) | archivero flags images nobody took | Accepted |
| [0007](0007-known-file-filter.md) | Known-file filter from public hash sets | Accepted |
| [0008](0008-exact-os-versions.md) | Exact OS versions from the files that state them | Accepted |

0001–0003 were recorded after v0.1 was built; 0004 describes v0.2 (zones and provenance); 0005 and 0006 are
milestone M0, the media index that memento reads; 0007 and 0008 complete the system-file side of v0.2.
