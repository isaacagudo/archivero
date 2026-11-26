"""Archives and disk images: list and hash their contents WITHOUT extracting them.

Two levels:

* list     Read only the index (zip central directory, 7z header, ISO directory,
           7-Zip listing of a disk image). Cheap. Gives names, sizes, dates and,
           for many formats, CRC32. Compressed tarballs have no index, so they
           are only listed when smaller than `archive_stream_max_gb`.
* inspect  Stream every member through BLAKE2b in memory (nothing is written to
           disk), so members can be compared exactly with files on disk.

Backends, tried in order for each format:
  zip, tar            -> Python standard library (always available)
  7z, rar, cab, iso,
  xar, cpio           -> libarchive-c (pip install libarchive-c), then 7-Zip CLI
  dmg, vmdk, vhd(x),
  vdi, qcow2, wim, img-> 7-Zip CLI (7zz / 7z / 7za)
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .categories import classify
from .dates import best_date, from_filename, ts_to_str
from .util import Progress, human

CHUNK = 1024 * 1024

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz",
                ".tar.zst", ".tar.lz", ".tar.lzma")
FORMAT_BY_EXT = {
    "zip": "zip", "7z": "7z", "rar": "rar", "cab": "cab", "iso": "iso", "xar": "xar",
    "cpio": "cpio", "dmg": "image", "vmdk": "image", "vhd": "image", "vhdx": "image",
    "vdi": "image", "qcow2": "image", "wim": "image", "img": "image", "toast": "iso",
}


def container_format(name):
    """Container format for a file name, or None if it is not an archive/image."""
    low = name.lower()
    if low.endswith(TAR_SUFFIXES):
        return "tar"
    ext = os.path.splitext(low)[1].lstrip(".")
    return FORMAT_BY_EXT.get(ext)


@dataclass
class Member:
    inner_path: str
    size: int
    mtime: Optional[float]
    crc32: Optional[str] = None
    is_dir: bool = False
    encrypted: bool = False


# ----------------------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------------------

class ZipBackend:
    name = "zipfile"
    formats = {"zip"}

    def available(self):
        return True

    def indexed(self, fmt, path):
        return True

    def list(self, path):
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                try:
                    mt = time.mktime(i.date_time + (0, 0, -1))
                except (OverflowError, ValueError):
                    mt = None
                yield Member(i.filename.rstrip("/"), i.file_size, mt, "%08X" % i.CRC, i.is_dir(),
                             bool(i.flag_bits & 0x1))

    def streams(self, path):
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                m = Member(i.filename.rstrip("/"), i.file_size, None, "%08X" % i.CRC, i.is_dir(),
                           bool(i.flag_bits & 0x1))
                if m.is_dir or m.encrypted:
                    continue
                with z.open(i) as f:
                    yield m, f


class TarBackend:
    name = "tarfile"
    formats = {"tar"}

    def available(self):
        return True

    def indexed(self, fmt, path):
        return path.lower().endswith(".tar")  # compressed tarballs must be fully decompressed

    def list(self, path):
        with tarfile.open(path, "r:*") as t:
            for ti in t:
                yield Member(ti.name.rstrip("/"), ti.size if ti.isfile() else 0, ti.mtime, None, ti.isdir())

    def streams(self, path):
        with tarfile.open(path, "r|*") as t:
            for ti in t:
                if not ti.isfile():
                    continue
                f = t.extractfile(ti)
                if f is not None:
                    yield Member(ti.name, ti.size, ti.mtime), f


class _Blocks:
    """File-like wrapper over libarchive's block iterator."""

    def __init__(self, blocks):
        self._it = iter(blocks)

    def read(self, n=-1):
        try:
            return bytes(next(self._it))
        except StopIteration:
            return b""


class LibarchiveBackend:
    name = "libarchive"
    formats = {"7z", "rar", "cab", "iso", "xar", "cpio", "zip", "tar"}

    def __init__(self):
        self._mod = None

    def available(self):
        if self._mod is None:
            try:
                import libarchive  # noqa: F401
                self._mod = libarchive
            except (ImportError, OSError):
                self._mod = False
        return bool(self._mod)

    def indexed(self, fmt, path):
        return fmt in ("7z", "iso", "zip", "xar", "cab", "rar")

    def list(self, path):
        with self._mod.file_reader(path) as a:
            for e in a:
                yield Member(e.pathname.rstrip("/"), e.size or 0, e.mtime, None, e.isdir)

    def streams(self, path):
        with self._mod.file_reader(path) as a:
            for e in a:
                if not e.isfile:
                    continue
                yield Member(e.pathname, e.size or 0, e.mtime), _Blocks(e.get_blocks())


class SevenZipBackend:
    name = "7zip"
    formats = {"7z", "rar", "cab", "iso", "xar", "cpio", "image", "zip", "tar"}

    def __init__(self):
        self.exe = None

    def available(self):
        if self.exe is None:
            self.exe = shutil.which("7zz") or shutil.which("7z") or shutil.which("7za") or False
        return bool(self.exe)

    def indexed(self, fmt, path):
        return not (fmt == "tar" and not path.lower().endswith(".tar"))

    def list(self, path):
        out = subprocess.run([self.exe, "l", "-slt", "-ba", "-p-", path], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, errors="replace", timeout=3600)
        if out.returncode not in (0, 1):
            raise OSError((out.stderr or out.stdout).strip().splitlines()[-1:] or "7-Zip error")
        rec = {}
        for line in out.stdout.splitlines() + [""]:
            if not line.strip():
                if "Path" in rec:
                    yield self._member(rec)
                rec = {}
                continue
            k, sep, v = line.partition(" = ")
            if sep:
                rec[k.strip()] = v.strip()

    @staticmethod
    def _member(rec):
        is_dir = rec.get("Folder") == "+" or rec.get("Attributes", "").startswith("D")
        mt = None
        if rec.get("Modified"):
            try:
                mt = time.mktime(datetime.strptime(rec["Modified"][:19], "%Y-%m-%d %H:%M:%S").timetuple())
            except ValueError:
                mt = None
        try:
            size = int(rec.get("Size") or 0)
        except ValueError:
            size = 0
        return Member(rec["Path"], size, mt, rec.get("CRC") or None, is_dir, rec.get("Encrypted") == "+")

    def streams(self, path):
        members = [m for m in self.list(path) if not m.is_dir and not m.encrypted]
        for m in members:
            p = subprocess.Popen([self.exe, "x", "-so", "-p-", path, m.inner_path], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                yield m, p.stdout
            finally:
                p.stdout.close()
                p.wait()


BACKENDS = [ZipBackend(), TarBackend(), LibarchiveBackend(), SevenZipBackend()]


def backend_for(fmt):
    for b in BACKENDS:
        if fmt in b.formats and b.available():
            return b
    return None


def missing_backend_hint(fmt):
    if fmt == "image":
        return "install 7-Zip (macOS: brew install sevenzip · Debian/Ubuntu: apt install 7zip)"
    return "pip install libarchive-c, or install 7-Zip"


# ----------------------------------------------------------------------------------------
# Listing and inspection
# ----------------------------------------------------------------------------------------

def _containers(con, paths=None):
    rows = con.execute("SELECT path, name, size, mtime FROM files ORDER BY path").fetchall()
    sel = set(os.path.abspath(p) for p in paths) if paths else None
    for path, name, size, mtime in rows:
        fmt = container_format(name)
        if fmt and (sel is None or path in sel):
            yield path, name, size or 0, mtime, fmt


def _save_members(con, cpath, members):
    con.execute("DELETE FROM members WHERE container=?", (cpath,))
    n = nd = total = 0
    enc = False
    for m in members:
        name = os.path.basename(m.inner_path) or m.inner_path
        if m.is_dir:
            nd += 1
            cat = sub = ext = date = None
        else:
            n += 1
            total += m.size or 0
            ext, cat, sub = classify(name)
            if cat == "video":
                cat = "video_short"  # refined only if the member is ever probed
            date, _ = best_date([(from_filename(name), "filename"), (ts_to_str(m.mtime), "mtime")])
        enc = enc or m.encrypted
        con.execute("""INSERT OR REPLACE INTO members(container,inner_path,name,ext,size,mtime,crc32,
                       is_dir,encrypted,category,subtype,date_best) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cpath, m.inner_path, name, ext, m.size, m.mtime, m.crc32, int(m.is_dir),
                     int(m.encrypted), cat, sub, date))
    return n, nd, total, enc


def _set_container(con, path, fmt, backend, status, n=None, nd=None, total=None, note=""):
    con.execute("""INSERT INTO containers(path,format,backend,status,n_members,n_dirs,total_size,note,listed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET format=excluded.format, backend=excluded.backend,
                     status=excluded.status, n_members=excluded.n_members, n_dirs=excluded.n_dirs,
                     total_size=excluded.total_size, note=excluded.note, listed_at=excluded.listed_at""",
                (path, fmt, backend, status, n, nd, total, note, time.time()))


def list_containers(con, cfg, paths=None, force=False):
    """Read the index of every archive / disk image in the inventory (no extraction)."""
    max_stream = cfg["archive_stream_max_gb"] * 1024 ** 3
    done = {r[0]: r[1] for r in con.execute("SELECT path, status FROM containers")}
    todo = [c for c in _containers(con, paths) if force or done.get(c[0]) not in ("listed", "inspected")]
    prog = Progress()
    stats = {}
    for i, (path, name, size, mtime, fmt) in enumerate(todo, 1):
        prog.show("Listing %d/%d · %s" % (i, len(todo), name))
        b = backend_for(fmt)
        if b is None:
            status, note = "unsupported", missing_backend_hint(fmt)
            _set_container(con, path, fmt, None, status, note=note)
        elif not b.indexed(fmt, path) and size > max_stream:
            status, note = "deferred", "no index: listing needs full decompression (%s); use 'inspect'" % human(size)
            _set_container(con, path, fmt, b.name, status, note=note)
        else:
            try:
                n, nd, total, enc = _save_members(con, path, b.list(path))
                status = "listed"
                note = "some members are encrypted" if enc else ""
                _set_container(con, path, fmt, b.name, status, n, nd, total, note)
            except Exception as err:  # corrupt or unreadable archive
                status = "error"
                _set_container(con, path, fmt, b.name, status, note=str(err)[:300])
        stats[status] = stats.get(status, 0) + 1
        con.commit()
    prog.done("Archives and disk images: %d processed (%s)." % (
        len(todo), ", ".join("%s %d" % kv for kv in sorted(stats.items())) or "nothing new"))


def inspect_containers(con, cfg, paths=None, suggested=False, everything=False):
    """Hash every member by streaming it through memory (no extraction to disk)."""
    from .dupes import full_hash  # local import to avoid a cycle

    max_member = cfg["archive_member_max_gb"] * 1024 ** 3
    if suggested:
        from .analysis import Analysis
        an = Analysis(con, cfg)
        paths = [c["path"] for c in an.containers.values() if c["suggestion"] in ("inspect", "already_extracted")]
        if not paths:
            print("No archives are suggested for inspection.")
            return
    elif not paths and not everything:
        sys.exit("Say which archives to inspect: give paths, --suggested or --all.")
    targets = list(_containers(con, paths))
    prog = Progress()
    total_bytes = 0
    for i, (path, name, size, mtime, fmt) in enumerate(targets, 1):
        b = backend_for(fmt)
        if b is None:
            _set_container(con, path, fmt, None, "unsupported", note=missing_backend_hint(fmt))
            continue
        try:
            listed = con.execute("SELECT status FROM containers WHERE path=?", (path,)).fetchone()
            if not listed or listed[0] not in ("listed", "inspected"):
                n, nd, total, enc = _save_members(con, path, b.list(path))
                _set_container(con, path, fmt, b.name, "listed", n, nd, total)
            for m, f in b.streams(path):
                if m.size and m.size > max_member:
                    continue
                h = hashlib.blake2b(digest_size=20)
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    total_bytes += len(chunk)
                    prog.show("Inspecting %d/%d · %s · %s read · %.1f MB/s" % (
                        i, len(targets), name, human(total_bytes), total_bytes / 1048576 / max(prog.elapsed(), .001)))
                con.execute("UPDATE members SET full_hash=? WHERE container=? AND inner_path=?",
                            (h.hexdigest(), path, m.inner_path.rstrip("/")))
            con.execute("UPDATE containers SET status='inspected', inspected_at=? WHERE path=?", (time.time(), path))
        except Exception as err:
            con.execute("UPDATE containers SET status='error', note=? WHERE path=?", (str(err)[:300], path))
        con.commit()
    prog.done("Inspected %d archives (%s decompressed in memory)." % (len(targets), human(total_bytes)))

    # Hash the files on disk that could match an inspected member, so matches are exact.
    rows = con.execute("""SELECT id, path, size FROM files WHERE full_hash IS NULL AND size > 0 AND size IN
                          (SELECT size FROM members WHERE full_hash IS NOT NULL)""").fetchall()
    if rows:
        from .dupes import _phase
        _phase(con, rows, full_hash, "full_hash", "Hashing files that may match archive members",
               cfg["dupes_workers"], lambda s: s)
