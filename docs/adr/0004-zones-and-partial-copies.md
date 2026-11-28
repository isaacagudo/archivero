# 0004. Zones from OS layouts, tolerant of partial copies

- Status: Accepted
- Date: 2026-11-28

## Context

Old backups are copies of whole drives, of user profiles, or of a few hand-picked folders. The goal is to keep
personal data and drop operating system files, programs and caches, and to say where each copy came from.

## Decision

- Directory layouts of Windows (XP to 11), macOS and Linux are described as **scored markers** and **zone rules**
  (`layouts.py`), from forensic sources (ForensicArtifacts, KAPE targets, UAC, FHS, XDG).
- Every file gets a zone: `user_data`, `user_appdata`, `cache`, `system`, `applications`, `trash` or `unknown`.
- A layout is recognized from **partial evidence**: a copy of `Documents and Settings/<user>` alone is enough to be a
  Windows XP profile; nothing requires a full drive.
- **Provenance clues** (vendor tools and drivers, model names, software era, personal-file years) produce a
  description such as "Windows XP drive, probably a Dell Latitude D630, files from 2006–2008".
- Zones drive cleanup proposals and the reorganization plan. Details in [OS_LAYOUTS.md](../OS_LAYOUTS.md).

## Consequences

- Rules are data: adding an OS version or a vendor clue is a change in `layouts.py`, not in the code.
- Provenance is a guess and is always worded as one ("probably").
