# 0001. Read-only on the source

- Status: Accepted
- Date: 2026-11-28

## Context

archivero runs over the only copies of years of files (NAS backups, old disks). A bug or a wrong guess about what
is obsolete must never cost data.

## Decision

- archivero never deletes, moves or modifies source files.
- Cleanup is a list of **proposals** (`cleanup_proposals.csv`, with an `approve` column), never an action.
- `plan` writes an editable CSV; `apply` only **copies**, and only with `--execute`.
- Destructive actions, if they ever exist, will be separate commands, logged and undoable.

## Consequences

- The user always decides; archivero can be run on a backup with no risk.
- Freeing space needs a second step outside archivero (or a future, explicit command).
