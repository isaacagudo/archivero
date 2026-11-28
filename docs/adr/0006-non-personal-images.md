# 0006. archivero flags images nobody took

- Status: Accepted (implemented in v0.2, milestone M0)
- Date: 2026-11-28

## Context

Backups are full of images that are not personal photos: application icons and interface graphics, OS wallpapers
and sample pictures, stock images, cached web images. A photo catalog should not show them.

## Decision

- The media index flags non-personal images, using the zones archivero already knows (system, applications,
  cache), plus size and name rules (tiny images, icon folders, known sample-picture names).
- The flag is data, not a deletion: memento hides flagged images by default and the user can show them.

## Consequences

- The rule lives in archivero, so every tool on top of the index sees the same answer.
- Will improve with the known-file filter ([0007](0007-known-file-filter.md)), which recognizes stock OS images by hash.
