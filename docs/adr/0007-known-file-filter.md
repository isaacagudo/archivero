# 0007. Known-file filter from public hash sets

- Status: Proposed
- Date: 2026-11-28

## Context

Zones recognize OS and program files by location. Files copied out of their place (a `Program Files` folder
inside `Backup/old`) or loose installers are only recognized by content. Forensic hash sets list millions of known
OS and software files.

## Decision

- Optional filter that marks files whose hash is in a known-file set: CIRCL hashlookup through its offline Bloom
  filter (about 700 MB, no network) or NSRL RDSv3; the online CIRCL API only as an explicit option.
- Needs SHA-1 (or MD5) of candidate files, computed only when the filter is on.
- Known files feed cleanup proposals like the `system` and `applications` zones.

## Consequences

- Works offline by default; nothing is sent unless the user enables the API.
- Next step of v0.2, together with exact OS versions from `etc/os-release` and `SystemVersion.plist`.
