<p align="center">
  <img src="docs/img/banner.svg" alt="archivero" width="820">
</p>

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-4a78c2">
  <img alt="No required dependencies" src="https://img.shields.io/badge/dependencies-none-3fa37a">
  <img alt="Read-only" src="https://img.shields.io/badge/source-read--only-1f2937">
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-e8a33d">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-6b7280">
</p>

<p align="center">
  <b>That old NAS. That backup drive. That "Misc" folder with fifteen years of history.</b><br>
  archivero walks through it, works out what each folder is, finds what is duplicated or obsolete,<br>
  and proposes how to reorganize it by type and date. <b>You decide. It never deletes anything.</b>
</p>

<p align="center"><sub><i>archivero</i> /ar.tʃiˈβe.ɾo/ is Spanish for <i>archivist</i> and, in Mexico, for a
<i>filing cabinet</i>.</sub></p>

---

## ✨ What it finds

| | |
|---|---|
| 📸 **Photos and videos** | The real date of every photo (EXIF, the file name or the modification date). Tells home videos apart from movies |
| 🎵 **Music** | Album folders and, if you want, artist and album from the ID3 tags |
| 📄 **Documents** | PDFs, Word files, spreadsheets and slides, grouped by year |
| 🧑‍💻 **Code projects** | Recognized as a whole (`package.json`, `.git`, `pom.xml`…), without getting lost in `node_modules` |
| ✉️ **Email** | Thunderbird profiles and their **mbox** files, even without an extension, plus loose PST and EML files |
| 🗜️ **Old software** | Folders of installers, ISOs and `.app` bundles that nobody has touched in years |
| 🖥️ **Old computers** | Recognizes copied Windows, macOS and Linux drives and profiles, **even partial copies**, keeps personal files apart from system files, and says where they came from: *"probably a Dell laptop (Latitude D630), Windows XP, personal files from 2006–2008"* |
| 🗃️ **Archives and disk images** | Looks inside zip, tar, 7z, rar, ISO, DMG, VMDK… **without extracting them**, and tells you whether a backup zip is already extracted somewhere else |
| 👯 **Duplicates** | Confirmed by content in two passes, with a sensible rule for which copy to keep |
| 🧹 **Junk** | `.DS_Store`, `Thumbs.db`, network trash folders, caches and regenerable dependencies |

## 🖼️ What it looks like

<p align="center">
  <img src="docs/img/report.png" alt="archivero HTML report" width="820">
</p>

Besides the HTML report, everything is exported to **CSV**, ready for a spreadsheet.

## 🚀 Quick start

```bash
git clone https://github.com/isaacagudo/archivero.git
cd archivero
pip install .                 # or: pipx install .   (extras: pip install ".[all]")

archivero scan   /path/to/nas --db nas.db --archives   # 1. walk and classify (+ archive indexes)
archivero dupes  --db nas.db                             # 2. confirm duplicates by hash
archivero report --db nas.db --out report                # 3. HTML report + CSV
open report/report.html
```

### Archives and disk images, without extracting

```bash
archivero archives list    --db nas.db               # read indexes only: names, sizes, dates, CRC32
archivero archives inspect --db nas.db --suggested   # hash members in memory, only where it pays off
```

`list` is cheap: it reads the zip central directory, the 7z header or the ISO
directory, never the data. From that alone archivero tells you, for each archive:

- **Already extracted.** Its files are already in a folder on disk (it even notices
  when that folder was renamed).
- **Worth inspecting.** It holds photos, documents or mail that exist nowhere else.
- **Software or OS image.** Installers and system files.

`inspect` streams every member through a hash in memory, with no temporary files.
That confirms or rules out the matches: an archive is only proposed for cleanup when
**every file is confirmed by hash** to exist on disk.

| Formats | Backend |
|---|---|
| zip, tar, tar.gz/bz2/xz | Python standard library (built in) |
| 7z, rar, cab, iso, xar, cpio | [`libarchive-c`](https://pypi.org/project/libarchive-c/) (`pip install ".[archives]"`) or 7-Zip |
| dmg, vmdk, vhd/vhdx, vdi, qcow2, wim, img | [7-Zip](https://www.7-zip.org/) command line (`brew install sevenzip`, `apt install 7zip`) |

Happy with the proposal? Generate a plan and apply it **by copying** into a new
structure:

```bash
archivero plan  --db nas.db --dest /Volumes/New --out plan.csv   # editable CSV
archivero apply --plan plan.csv                                   # dry run
archivero apply --plan plan.csv --execute                         # real copy
```

Want to try it without touching your data? `python3 scripts/demo_tree.py /tmp/demo`
builds a sample tree that looks like 19 GB but uses only 160 MB of disk.

## 🧭 How it works

```mermaid
flowchart LR
    A[(Source<br/>NAS · drive)] -->|scan| B[Classification<br/>+ detectors]
    B --> D[(SQLite)]
    D -->|dupes| C[Size → quick hash<br/>→ full hash]
    C --> D
    D -->|report| R[report.html<br/>CSV]
    D -->|plan| P[plan.csv<br/>editable]
    P -->|apply| N[(New structure<br/>copy only)]
```

- **Resumable.** If it stops after three hours, run the same command again and it
  continues where it was.
- **Fast where it matters.** It only reads file contents when needed: hashes only for
  duplicate candidates, and EXIF, ID3 tags or video duration only if you ask.
- **Repeatable.** Reports and plans are built from the database in seconds, so you
  can try other thresholds without scanning again.

## 🛡️ Safety promise

- No command **deletes or moves** anything in the source.
- Cleanup is a **proposal** (`cleanup_proposals.csv`), with the reason for every item
  and an `approve` column for you to fill in.
- For every group of duplicates it states **which copy is kept and why**: the one
  inside a project, the one that does not look like a backup, the oldest.
- `apply` only copies, and never overwrites what is already there.

## 📦 What `report` produces

| File | Use it to |
|---|---|
| `report.html` | Browse a summary: cleanup, types, years, email, music, folders, duplicates |
| `cleanup_proposals.csv` | See what could go, how much space it frees, and why |
| `folders.csv` | Get every folder with its type ("Photos and videos", "Software", "Code project"…), size and dates |
| `files.csv` | Get one row per file: category, date, where the date came from, hash |
| `duplicates.csv` | Review groups of identical copies, with the one to keep marked |
| `units.csv` | List code projects and mail profiles, with their mailboxes |
| `sources.csv` | See every copied drive or profile, what it looks like and the evidence behind it |
| `containers.csv` | Review every archive and disk image: content, how much is already on disk, suggestion |
| `archive_members.csv` | Browse the files inside archives without opening them |

## ⚙️ Configuration

```toml
# archivero.toml  →  archivero report --config archivero.toml ...
[archivero]
obsolete_years = 6       # years untouched before software counts as obsolete
software_share = 0.8     # minimum share of software in a folder
long_video_min = 40      # minutes before a video counts as a movie (with --probe-video)

[categories]
code = ["cairo", "move"]
```

Optional extras: `pillow` (EXIF dates, `--exif`), `mutagen` (audio tags, `--tags`),
`libarchive-c` and 7-Zip (more archive and disk image formats) and ffmpeg's
`ffprobe` (video duration, `--probe-video`).

## 🔌 Extensible

Every rule that gives a folder its meaning is a **detector**: a small function that
looks at the names in a folder and says what it is. A new one, for Lightroom
catalogs, WhatsApp backups or virtual machines, takes a few lines. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 🗺️ Roadmap

- **v0.2. Personal data vs. system files** (in progress): OS layouts, zones and
  provenance are done; next is filtering known software with the NSRL and CIRCL
  hashlookup hash sets ([design](docs/OS_LAYOUTS.md)).
- **v0.3. Email, inside the mailboxes:** messages, senders and dates for every mbox,
  and messages duplicated across profiles.
- **v0.4:** whole duplicated folders (Merkle hash) and near-identical photos.
- **v0.5:** quarantine with `undo`, and hash verification when copying.
- **v0.6–0.7:** parallel scanning, tens of millions of files, interactive report,
  PyPI release.

Full details in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## 💡 Tips

- **NAS over NFS:** NFS v3 grants access by numeric UID. If your local user does not
  match the NAS user, [`docs/NAS_NFS.md`](docs/NAS_NFS.md) shows how to handle it on
  Linux and macOS.
- Run archivero as close to the data as you can: wired beats Wi-Fi, and NFS or SMB
  beat SSH.

## 🤝 Contributing

Ideas, new detectors and bug reports are welcome. Before opening a PR:

```bash
pip install ".[dev]"
python -m pytest
```

## 📄 License

[MIT](LICENSE) © Isaac Agudo
