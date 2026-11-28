# Telling personal data from system files

**Status: implemented in v0.2: zones and provenance (`layouts.py`, `zones.py`), exact OS
versions (`osversion.py`) and the known-file hash filter (section 2, `known.py`).** Old drives and NAS shares often contain whole
system disks copied as-is: a `C:` drive from 2006, a Mac home folder, a Linux root
partition. The goal is to keep what a *person* created (photos, documents, mail,
chats) and discard what an *operating system or installer* put there (executables,
libraries, caches), without opening millions of files by hand.

Digital forensics solved this problem long ago. archivero borrows two of its ideas:

1. **Know the layout.** Every OS version puts system files and user files in
   predictable places. Labelling each folder with a *zone* removes most of the
   noise at almost no cost.
2. **Know the files.** Public hash sets list millions of files shipped by operating
   systems and software. A file whose hash is on the list is not personal.

## 1. Layout knowledge: zones

Every file gets a `zone`:

| Zone | Meaning | Default proposal |
|---|---|---|
| `user_data` | Folders where people keep their things: Documents, Pictures, Desktop… | **Keep** |
| `user_appdata` | Per-user application data: mail profiles, browser bookmarks, chat logs, game saves | **Review**. It mixes gold (a Thunderbird profile) with junk |
| `cache` | Caches, temporary files, thumbnails, browser caches | Discard |
| `system` | OS binaries, libraries, drivers, fonts, logs | Discard |
| `applications` | Installed programs | Discard (list them for reference) |
| `trash` | Recycle bins | Discard after review |
| `unknown` | Anything not covered by a rule | Classified as today, by type and date |

### How a layout is recognized

archivero detects **OS roots** and **user homes** from a handful of names. It reads
no file contents, except the few files that state the exact version (see
[Exact versions](#exact-versions)).

| Detected | Evidence | Era / version hints |
|---|---|---|
| Windows system drive | `Windows/` plus `Program Files/` | `Documents and Settings/` → 2000/XP/2003. `Users/` → Vista or later. `Program Files (x86)/` → 64-bit. `Windows.old/` → an upgraded system (it contains an **older Users folder**). `Windows/System32/config/SOFTWARE` gives the exact version |
| Windows profile v1 (2000/XP) | `NTUSER.DAT` + `My Documents/` or `Local Settings/` | Profiles live in `Documents and Settings\<user>` |
| Windows profile v2 (Vista or later) | `NTUSER.DAT` + `AppData/` | Profiles live in `Users\<user>` |
| macOS root | `System/Library/`, `Applications/`, `Users/` | `System/Library/CoreServices/SystemVersion.plist` gives the exact version |
| macOS home | `Library/` + `Desktop/` + `Documents/` | `Library/Mobile Documents` = iCloud Drive |
| Linux/Unix root | `etc/`, `usr/`, `bin/` (or a symlink to `usr/bin`), `var/` | `etc/os-release` gives the distribution and version |
| Linux home | `.bashrc`, `.profile`, `.config/`, `.local/` | XDG user folders may be localized (`Documentos`, `Imágenes`…) |

### Exact versions

When a copied drive still holds the file where its OS records its version, `scan` reads it
(only at these paths, and only a few kilobytes) and the description uses it instead of the era
guess ([ADR 0008](adr/0008-exact-os-versions.md)):

| OS | File | Example |
|---|---|---|
| Windows | `Windows/System32/config/SOFTWARE` (registry hive, `Microsoft\Windows NT\CurrentVersion`) | Windows 7 Professional Service Pack 1, build 7601 |
| Windows 2000/XP | `Windows/System32/prodspec.ini` | Windows XP Professional |
| macOS | `System/Library/CoreServices/SystemVersion.plist` | Mac OS X 10.6.8 Snow Leopard, build 10K549 |
| Linux | `etc/os-release` (or `usr/lib/os-release`), then `etc/lsb-release`, `etc/*-release`, `etc/debian_version` | Ubuntu 10.04.4 LTS |

The list is data (`layouts.VERSION_FILES`); the first file found, in that order, wins.

### Windows

Profile folders moved in Vista. Windows 2000, XP and Server 2003 keep profiles in
`Documents and Settings`; Vista and later use `C:\Users`, with a
`Documents and Settings` junction pointing to `Users` for compatibility. Vista also
replaced XP's `Application Data` and `Local Settings` with a single `AppData` folder
split into `Roaming`, `Local` and `LocalLow`.

| Path (XP-era) | Path (Vista or later) | Zone |
|---|---|---|
| `Windows/`, `WINNT/` | `Windows/` | system |
| `Program Files/` | `Program Files/`, `Program Files (x86)/` | applications |
| `Documents and Settings/All Users/Application Data` | `ProgramData/` | system (review vendor folders) |
| `RECYCLER/` | `$Recycle.Bin/` | trash |
| `System Volume Information/`, `pagefile.sys`, `hiberfil.sys` | same, plus `swapfile.sys`, `Recovery/`, `PerfLogs/`, `$WINDOWS.~BT/` | system |
| `…\<user>\My Documents\` (`My Pictures`, `My Music`, `My Videos`) | `Users\<user>\Documents`, `Pictures`, `Music`, `Videos` | user_data |
| `…\<user>\Desktop`, `Favorites` | `Desktop`, `Downloads`, `Favorites`, `Contacts`, `Links`, `Saved Games`, `OneDrive` | user_data |
| `…\<user>\Application Data` | `AppData\Roaming` | user_appdata |
| `…\<user>\Local Settings\Application Data` | `AppData\Local`, `AppData\LocalLow` | user_appdata (mostly cache) |
| `Local Settings\Temp`, `Temporary Internet Files`, `History` | `AppData\Local\Temp`, `…\INetCache`, `…\Microsoft\Windows\History` | cache |
| `Cookies`, `NetHood`, `PrintHood`, `Recent`, `SendTo`, `Start Menu`, `Templates` | same names under `AppData\Roaming\Microsoft\Windows\` | system (per user) |
| `Default User`, `LocalService`, `NetworkService` | `Default`, `Public` (Public is user_data) | system |

### macOS

System Integrity Protection (OS X 10.11 and later) protects `/System`, `/usr` (except
`/usr/local`), `/bin`, `/sbin`, `/var` and the apps that come preinstalled with
macOS. Third-party software may still write to `/Applications`, `/Library` and
`/usr/local`. Those protected paths are, by construction, never personal data.

| Path | Zone |
|---|---|
| `System/`, `bin/`, `sbin/`, `usr/` (except `usr/local`), `private/var/`, `cores/` | system |
| `Library/` (root level) | system (review `Library/Application Support` for vendor data) |
| `Applications/`, `~/Applications/` | applications |
| `.Spotlight-V100`, `.fseventsd`, `.DocumentRevisions-V100`, `.Trashes`, `.vol` | system / trash |
| `~/Desktop`, `~/Documents`, `~/Downloads`, `~/Movies`, `~/Music`, `~/Pictures`, `~/Public` | user_data |
| `~/Pictures/*.photoslibrary`, `iPhoto Library` | user_data, **one unit**: a package that must be copied whole |
| `~/Library/Mobile Documents` (iCloud Drive), `~/Library/Mail`, `~/Library/Messages` | user_appdata, high value |
| `~/Library/Application Support`, `~/Library/Containers`, `~/Library/Preferences` | user_appdata |
| `~/Library/Caches`, `~/Library/Logs`, `~/.Trash` | cache / trash |

### Linux and other Unix systems

The Filesystem Hierarchy Standard separates static files (binaries, libraries,
documentation) under `/usr` from variable data under `/var`. User homes live in
`/home`. In user homes, the XDG Base Directory specification sets
`~/.config` (settings), `~/.local/share` (data), `~/.local/state` and `~/.cache`
(disposable).

| Path | Zone |
|---|---|
| `bin/`, `sbin/`, `lib*/`, `usr/` (except `usr/local` contents to review), `boot/`, `opt/` | system / applications |
| `proc/`, `sys/`, `dev/`, `run/`, `tmp/`, `var/cache/`, `var/tmp/` | system / cache |
| `etc/` | system (keep a copy if you want to rebuild the machine) |
| `var/log/` | system |
| `var/mail/`, `var/spool/mail/` | user_appdata |
| `var/lib/` | system (databases such as `mysql` or `postgresql` are a special case: review) |
| `home/<user>/` XDG user folders (`Desktop`, `Documents`, `Downloads`, `Music`, `Pictures`, `Videos`, and their localized names) | user_data |
| `~/.config`, `~/.local/share`, `~/.mozilla`, `~/.thunderbird`, `~/.ssh`, `~/.gnupg` | user_appdata (`.ssh` and `.gnupg` are **sensitive**) |
| `~/.cache`, `~/.local/share/Trash` | cache / trash |

### Partial copies

Backups rarely contain whole drives: often only `Documents and Settings` and
`Program Files` were copied, or a single profile. Recognition therefore never
requires a complete layout:

- **Scored markers.** Every folder is judged by the names it contains, and each
  marker adds a weight (`Documents and Settings` = 3, `Program Files` = 2,
  `pagefile.sys` = 3…). A folder becomes a *Windows system drive*, *Windows profile*,
  *Mac home*, etc. as soon as the evidence reaches a threshold.
- **Anchored and free rules.** Zone rules are either anchored to a detected folder
  (`win_root:windows` → system: only a `Windows` folder right below a recognized
  drive) or free (`any:appdata/local/temp` → cache: specific enough to apply
  anywhere, even in a lone copied profile).
- **Implied drives.** A profile found under `Users`, `Documents and Settings` or
  `home` implies the drive above it, even if nothing else of it was copied.

### Data format

Layouts are plain data in [`archivero/layouts.py`](../archivero/layouts.py), so adding
an OS, a version, a localized folder name or a vendor clue needs no other code:

```python
SCOPES["win_profile"]["markers"]["mis documentos"] = 2      # evidence + weight
SCOPES["win_profile"]["era"]["mis documentos"] = "2000/XP"  # what it says about the era
ZONE_RULES.append(("any:appdata/local/temp", "cache"))       # free rule
ZONE_RULES.append(("win_root:program files", "applications"))  # anchored rule
```

The deepest matching rule wins; folders inherit the zone of their parent unless a
deeper rule overrides it.

## 1b. Provenance: "where did this come from?"

Each recognized drive or profile becomes a **source**, described from the evidence
found inside it:

| Clue | Examples | What it suggests |
|---|---|---|
| Layout era | `Documents and Settings`, `boot.ini` → 2000/XP; `AppData`, `$Recycle.Bin` → Vista or later; `swapfile.sys` → 8 or later | Windows generation, refined with file dates ("probably Windows 7") |
| Vendor tools | `Dell QuickSet`, `SWSetup` (HP), `ThinkVantage`, `VAIO Care`, Boot Camp | Maker of the computer |
| Model names | Latitude D630, ThinkPad T61, Satellite A200, MacBook Pro | Model |
| Laptop hints | Synaptics/ALPS touchpad drivers, power managers, Wi-Fi drivers | Laptop rather than desktop |
| Hardware | NVIDIA, ATI, Realtek, Intel wireless | Shown as evidence |
| Software | Office 12 (2007), Windows Mail (Vista), MSN Messenger, Nokia PC Suite | Era of use |
| Dates | Years of the personal files (5th–95th percentile, peak year) | When it was used |

The result reads like *"Windows system drive (probably Windows XP), probably a Dell
laptop (Latitude D630), personal files from 2006–2008 (most in 2007)"*, and every
Firefox or Thunderbird profile inherits it: *"Firefox profile from Windows 2000/XP;
source: …"*. When most files share one modification day, the description warns that
the dates are probably those of a copy.

The same engine runs on the members of archives and disk images, so a zipped or
imaged old drive is described without extracting it.

## 2. File knowledge: known-file hash sets

| Source | What it is | How archivero uses it |
|---|---|---|
| **NIST NSRL RDS** | NIST's Reference Data Set: hashes of software files, published every quarter. The RDSv3 sets are split into *modern*, *legacy*, *android* and *ios*, each also available as a smaller *minimal* edition. Files are large | `--nsrl RDS.db`: the SQLite file is opened read-only and its `FILE` view is joined with the candidates; nothing is imported |
| **CIRCL hashlookup** | A free public API run by CIRCL, Luxembourg's CERT. It looks up hashes in NSRL and other sources, including common Windows 10/11 builds and Linux distribution packages | `--online`: opt-in only, and only for executables and libraries that the offline sets do not know, each sent at most once; never documents or photos |
| **CIRCL hashlookup Bloom filter** | Offline Bloom filter of the hashlookup database (`hashlookup-full.bloom`, about 1 GB), in the DCSO format, holding upper-case SHA-1s | `--bloom hashlookup-full.bloom`, **the default way.** Checked in place (mmap), and no hash ever leaves the machine |

Design rules:

- **Only hash what could be software.** Executables, libraries, disk images and
  installers anywhere; code, configuration, unknown types and files with no
  extension outside a `user_data` zone. Documents, photos, mail, audio, keys and
  databases are not looked up: a known hash for them is meaningless, and sending
  them would leak information. Stock pictures can be looked up with `--images`,
  outside user folders and only in the offline sets.
- These sets use **SHA-1/MD5**, so the known-file check computes SHA-1 for those
  candidates (in the same read as BLAKE2b), once per content.
- A **known** file joins the cleanup proposals as `known_software` (alone, or as the
  top-most folder where known files hold most of the bytes), counts as software and
  never as personal. An **unknown** executable in a system zone is listed for review:
  it could be custom software or tampering.

## 3. Personal-data signal

The final verdict per file combines the three signals:

```
zone (layout)  +  category (type)  +  known hash?
   user_data       photo/document       no          → personal (keep)
   user_appdata    email/database       no          → personal, review
   system          executable           yes         → system (discard)
   system          executable           no          → unknown software (review)
   unknown         executable           yes         → known software, copied out of place (discard)
   unknown         photo                no          → personal (keep)
```

The report's **"Personal data"** and **"Where this came from"** views show where the
irreplaceable things are, per zone and per source machine or profile.

## 4. Other forensic projects worth reusing

- **ForensicArtifacts** (`ForensicArtifacts/artifacts`). A community, machine-readable
  YAML knowledge base of artifact locations, used by GRR and Plaso. It lists where
  *interesting* artifacts live (browser history, chat databases, mail stores) per
  OS, so archivero could import it to tag `user_appdata` files that are worth
  keeping.
- **KAPE targets** (`EricZimmerman/KapeFiles`). Community-maintained Windows
  collection targets (`.tkape`), including directory-traversal targets for
  documents, pictures, audio, video and SQLite databases. A good checklist of
  Windows application data paths.
- **UAC** (Unix-like Artifacts Collector). YAML artifact definitions for Linux, macOS,
  the BSDs, Solaris, AIX, ESXi and others, tagged with `supported_os`. The
  equivalent of KAPE for Unix-like systems.

## References

- NIST NSRL downloads: https://www.nist.gov/itl/ssd/software-quality-group/national-software-reference-library-nsrl/nsrl-download
- CIRCL hashlookup: https://www.circl.lu/services/hashlookup/ · Bloom filter: https://data.public.lu/en/datasets/circl-hashlookup-bloomfilter/
- ForensicArtifacts: https://github.com/ForensicArtifacts/artifacts
- KapeFiles: https://github.com/EricZimmerman/KapeFiles
- UAC: https://github.com/tclahr/uac
- Microsoft, profiles directory: https://learn.microsoft.com/en-us/windows/win32/shell/profiles-directory
- Apple, System Integrity Protection: https://support.apple.com/102149
- Filesystem Hierarchy Standard 3.0: https://refspecs.linuxfoundation.org/FHS_3.0/fhs-3.0.html
- XDG Base Directory specification: https://specifications.freedesktop.org/basedir-spec/latest/
