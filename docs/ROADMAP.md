# Roadmap

Every release must leave the tool usable and tested. Anything that touches the
source (deleting, moving) comes as late as possible and always with an undo.

## v0.1: inventory and proposals (done)

- [x] Resumable `scan` with classification by extension and best available date
- [x] Detectors: code projects, Thunderbird and Firefox profiles, mbox files, music
      folders, software bundles, pruned folders measured in bulk
- [x] Two-pass `dupes` (quick hash, then full hash), in parallel
- [x] Cleanup proposals: obsolete software, old installers, regenerable folders,
      trash, duplicates, system junk
- [x] `report` (HTML + CSV), `plan` (editable CSV), `apply` (copy only)
- [x] Archives and disk images without extracting: `archives list` (index only) and
      `archives inspect` (members hashed in memory); zip/tar built in, 7z/rar/cab/iso
      through libarchive, dmg/vmdk/vhd/vdi/qcow2/wim through 7-Zip
- [x] Archive ↔ folder matching (name+size to suggest, hash to propose cleanup),
      with suggestions: already extracted, inspect, software image
- [x] End-to-end tests on synthetic trees, plus a demo tree generator

## v0.2: personal data vs. system files

Goal: on a copied system disk, find what a person created and set aside what the OS
and installers put there. Design in [`OS_LAYOUTS.md`](OS_LAYOUTS.md).

- [ ] Detect OS roots and user homes (Windows 2000/XP and Vista or later, macOS,
      Linux), guessing the era or version from the layout, `etc/os-release` and
      `SystemVersion.plist`
- [ ] Declarative TOML layouts that give every file a **zone** (`user_data`,
      `user_appdata`, `cache`, `system`, `applications`, `trash`)
- [ ] Known-file filter: SHA-1 of software candidates checked against the CIRCL
      hashlookup Bloom filter (offline by default) and, optionally, NSRL RDSv3 or
      the online API
- [ ] A "Personal data" view in the report, grouped by user profile, source machine
      and year
- [ ] `Windows.old`, `*.photoslibrary` packages and iCloud Drive handled as special
      cases
- [ ] Apply layouts and the known-file filter to **members of disk images** too, so a
      `.vmdk` or `.dmg` of an old machine is triaged without being extracted

## v0.3: email, inside the mailboxes

- [ ] `mail` command: stream mbox, EML and PST files and fill a `mailboxes` table
      (message count, date range, top senders and recipients, attachment volume,
      empty boxes, boxes holding only deleted messages)
- [ ] Optional `messages` table (Message-ID, date, from, subject, hash) to find
      **messages duplicated across profiles and backups**, even when the mbox files
      differ
- [ ] Proposal: profiles whose messages all exist elsewhere → `duplicate`
- [ ] Optional export to `.eml` or to one mbox per year
- [ ] PST support through `libpff` or `readpst` when available

## v0.3.5: deeper into archives and images

- [ ] Nested archives (a zip inside a zip inside an ISO), up to a configurable depth
- [ ] Partitioned raw images and forensic formats (E01, raw `dd` with MBR/GPT,
      NTFS/HFS+/APFS/ext) through dfVFS or pytsk3
- [ ] Classify members by content when they are inspected (EXIF dates, video
      duration), not only by name
- [ ] `archives extract`: extract only selected members, or only what is missing
      on disk, into a chosen folder

## v0.4: folder-level duplicates and photos

- [ ] Merkle folder hash to find **whole duplicated folders** (copies of a project,
      "Photos (copy)"), even when they were renamed
- [ ] Near-identical folders: percentage of shared files
- [ ] Richer EXIF: camera, optional GPS, grouping into events
- [ ] Optional perceptual hashing for near-duplicate photos

## v0.5: safe actions

- [ ] `quarantine`: move the proposals marked `approve = yes` to a quarantine folder
      **on the same volume**, with a log for `undo`
- [ ] `apply --move` within one volume, also with a log and `undo`
- [ ] `apply --verify`: check hashes after copying

## v0.6: scale

- [ ] Parallel scanning by subtree, to hide NFS and SMB latency
- [ ] Aggregates in SQL instead of memory, for tens of millions of files
- [ ] Incremental inventories: what changed since the last scan
- [ ] Several roots in one database, to compare the NAS with an external drive and
      see what is already safe
- [ ] Run it on the server itself (single binary or container)

## v0.7: interface and community

- [ ] Interactive report: filter, mark proposals and export the approved CSV from the
      browser (or serve the database with Datasette)
- [ ] Translatable messages (en, es, …)
- [ ] PyPI release and CI (GitHub Actions) on Linux, macOS and Windows
- [ ] Detector writing guide, with examples (Lightroom catalogs, WhatsApp backups,
      virtual machines, Moodle backups…)

## Ideas without a release

- `libmagic` content sniffing for files with no extension
- Flag sensitive personal documents (ID scans, payslips, private keys) for
  encryption or special handling
- Folder summaries from a local LLM ("what is this folder?"), from file names and a
  small sample
