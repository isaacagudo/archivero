# 0008. Exact OS versions from the files that state them

- Status: Accepted (implemented in v0.2)
- Date: 2026-11-28

## Context

Layout markers only give an era ("Vista or later"), refined with file dates into a guess ("probably Windows 7").
Most copied system drives also keep the file where their OS records its exact version.

## Decision

- `scan` reads a version file only when it sits at a known path below a folder (`layouts.VERSION_FILES`):
  `Windows/System32/config/SOFTWARE` (the registry hive: `ProductName`, service pack or display version, build),
  `Windows/System32/prodspec.ini` (2000/XP), `System/Library/CoreServices/SystemVersion.plist` (macOS),
  `etc/os-release`, `usr/lib/os-release`, `etc/lsb-release` and the distribution release files (Linux).
- Text files are read only up to 64 KB. The hive is not read whole: a small read-only regf reader seeks to
  `Microsoft\Windows NT\CurrentVersion`, a few kilobytes even in a 100 MB hive. No dependency is added.
- What the file says is stored in `files.extra` (`os_version`), so the schema does not change and `report` keeps
  reading only the database. A database scanned before re-reads these files on the next scan.
- The analysis attaches a version only to the drive the file belongs to (the folder above the expected path must
  be a detected source); the first file in `VERSION_FILES` order wins. The version replaces the era guess in the
  description and is listed in the evidence and in `sources.csv`.

## Consequences

- "Windows system drive (Windows 7 Professional Service Pack 1, build 7601)" instead of "probably Windows 7".
- Windows 11 still reports "Windows 10" in `ProductName`; builds 22000 and later are renamed.
- Drives inside archives and disk images keep the era guess: reading a member's content needs a streaming pass.
