"""Known-file filter (ADR 0007): recognize OS and software files by their SHA-1.

'archivero known' hashes the files that could be software (by category and zone, see
is_candidate) and looks them up in public sets of known files:

- the CIRCL hashlookup Bloom filter (offline, the default): a DCSO-format file of about
  1 GB that is checked in place, with mmap, so nothing is loaded and nothing leaves the
  machine;
- NIST NSRL RDSv3 SQLite databases (offline), opened read-only;
- the CIRCL hashlookup API (online), only with --online and only for executables.

Hashing is resumable and done once per content: results live in the 'known' table,
keyed by the same BLAKE2b hash as files.full_hash, so new sets can be checked later
without reading the files again.
"""

import hashlib
import json
import mmap
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .categories import is_software
from .db import get_meta, require_root, set_meta
from .util import Progress, human
from .zones import disk_layout

CHUNK = 1024 * 1024
API_URL = "https://hashlookup.circl.lu/bulk/sha1"
API_BATCH = 100
BLOOM_URL = "https://cra.circl.lu/hashlookup/hashlookup-full.bloom"

# Never looked up: personal content (a known hash says nothing about a photo or a letter) and
# things too sensitive to hash for a third party (keys, databases, mail).
NEVER = {"raw", "video_short", "video_long", "audio", "pdf", "document", "spreadsheet", "presentation",
         "email", "design", "certificate", "database"}
# Memory and hibernation files: large, and full of whatever was in RAM.
NEVER_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}
# Looked up outside user folders: what programs and operating systems ship besides executables.
SOFTWARE_LIKE = {"code", "config", "other", "no_extension"}


def is_candidate(category, name, zone, images=False):
    """Should this file be looked up? Software anywhere; software-like files outside user folders."""
    if category in NEVER or name.lower() in NEVER_NAMES:
        return False
    if category == "image":
        return images and zone != "user_data"
    if is_software(category, name):
        return True
    return category in SOFTWARE_LIKE and zone != "user_data"


def online_eligible(category):
    """Only executables and libraries are ever sent to the online API (docs/OS_LAYOUTS.md)."""
    return category == "executable"


# ------------------------------------------------------------------ hash sets

class BloomFilter:
    """A Bloom filter in the DCSO format (github.com/DCSO/bloom), as CIRCL publishes it.

    Header: six little-endian 64-bit words (version, n, p as a float, k, m, N), then the
    bit array as ceil(m / 64) little-endian words. Keys are hashed with 64-bit FNV-1 and
    the k positions follow h = h * g mod 2^64 mod P.
    """

    P = 18446744073709551557   # largest prime below 2^64
    G = 18446744073709550147
    MASK = (1 << 64) - 1

    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(48)
        if len(head) < 48:
            raise ValueError("%s: not a Bloom filter (too short)" % path)
        flags, self.n, self.p, self.k, self.m, self.count = struct.unpack("<QQdQQQ", head)
        if flags & 0xFF != 1 or not self.m or not 0 < self.k < 64:
            raise ValueError("%s: not a DCSO Bloom filter" % path)
        words = (self.m + 63) // 64
        if os.fstat(self._f.fileno()).st_size < 48 + 8 * words:
            raise ValueError("%s: truncated Bloom filter" % path)
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)

    @staticmethod
    def fnv1_64(data):
        h = 0xCBF29CE484222325
        for b in data:
            h = ((h * 0x100000001B3) & BloomFilter.MASK) ^ b
        return h

    def positions(self, key):
        h = self.fnv1_64(key) % self.P
        for _ in range(self.k):
            h = ((h * self.G) & self.MASK) % self.P
            yield h % self.m

    def __contains__(self, key):
        mm = self._mm
        for pos in self.positions(key):
            word = struct.unpack_from("<Q", mm, 48 + 8 * (pos // 64))[0]
            if not word >> (pos % 64) & 1:
                return False
        return True

    def close(self):
        self._mm.close()
        self._f.close()

    @classmethod
    def write(cls, path, keys, n=None, p=0.0001):
        """Build a filter (used by the tests and to make small private sets)."""
        import math
        keys = list(keys)
        n = n or max(len(keys), 1)
        m = int(math.ceil(abs(n * math.log(p) / (math.log(2) ** 2))))
        k = int(math.ceil(math.log(2) * m / n))
        words = [0] * ((m + 63) // 64)
        bf = cls.__new__(cls)
        bf.m, bf.k = m, k
        for key in keys:
            for pos in bf.positions(key):
                words[pos // 64] |= 1 << (pos % 64)
        with open(path, "wb") as f:
            f.write(struct.pack("<QQdQQQ", 1, n, p, k, m, len(keys)))
            f.write(struct.pack("<%dQ" % len(words), *words))


class BloomSet:
    offline = True

    def __init__(self, path):
        self.bf = BloomFilter(path)
        self.label = "bloom:" + _stem(path)
        self.info = {"label": self.label, "path": os.path.abspath(path), "entries": self.bf.count,
                     "false_positive_rate": self.bf.p}

    def lookup(self, sha1s):
        return {s: None for s in sha1s if s.encode() in self.bf}

    def close(self):
        self.bf.close()


class NsrlSet:
    """An NSRL RDSv3 SQLite database, opened read-only. Its FILE view has upper-case SHA-1s."""

    offline = True

    def __init__(self, path):
        if not os.path.isfile(path):
            raise ValueError("%s: file not found" % path)
        self.con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
        try:
            cols = {r[1].lower() for r in self.con.execute("PRAGMA table_info(FILE)")}
        except sqlite3.DatabaseError as err:
            raise ValueError("%s: not an SQLite database (%s)" % (path, err))
        if "sha1" not in cols:
            raise ValueError("%s: no FILE table with a sha1 column (is it an NSRL RDSv3 database?)" % path)
        self.has_name = "file_name" in cols
        self.label = "nsrl:" + _stem(path)
        ver = None
        try:
            ver = self.con.execute("SELECT version FROM VERSION LIMIT 1").fetchone()
        except sqlite3.DatabaseError:
            pass
        self.info = {"label": self.label, "path": os.path.abspath(path), "version": ver[0] if ver else None}

    def lookup(self, sha1s):
        # One pass over FILE for the whole batch, whether or not the set has an index on sha1.
        self.con.execute("CREATE TEMP TABLE IF NOT EXISTS cand (sha1 TEXT PRIMARY KEY)")
        self.con.execute("DELETE FROM temp.cand")
        self.con.executemany("INSERT OR IGNORE INTO temp.cand VALUES (?)", [(s,) for s in sha1s])
        name = "file_name" if self.has_name else "NULL"
        out = {}
        for sha1, fname in self.con.execute(
                "SELECT sha1, %s FROM FILE WHERE sha1 IN (SELECT sha1 FROM temp.cand)" % name):
            out.setdefault(sha1, fname)
        return out

    def close(self):
        self.con.close()


class OnlineSet:
    """CIRCL hashlookup bulk API. Only executables are sent; see online_eligible()."""

    offline = False
    label = "circl-api"

    def __init__(self, url=API_URL, opener=None):
        self.url = url
        self.opener = opener or urllib.request.urlopen
        self.info = {"label": self.label, "url": url}

    def lookup(self, sha1s):
        out = {}
        for i in range(0, len(sha1s), API_BATCH):
            batch = sha1s[i:i + API_BATCH]
            req = urllib.request.Request(self.url, data=json.dumps({"hashes": batch}).encode(),
                                         headers={"Content-Type": "application/json", "Accept": "application/json",
                                                  "User-Agent": "archivero/" + __version__})
            with self.opener(req, timeout=60) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            for r in res if isinstance(res, list) else []:
                sha1 = isinstance(r, dict) and (r.get("SHA-1") or "").upper()
                if sha1:
                    out[sha1] = r.get("FileName")
        return out

    def close(self):
        pass


def _stem(path):
    base = os.path.basename(path)
    for ext in (".bloom", ".db", ".sqlite", ".sqlite3"):
        if base.lower().endswith(ext):
            return base[:-len(ext)]
    return base


# ------------------------------------------------------------------ hashing

def hash_pair(path):
    """(BLAKE2b-160 hex, upper-case SHA-1 hex) in one read."""
    b, s = hashlib.blake2b(digest_size=20), hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            b.update(chunk)
            s.update(chunk)
    return b.hexdigest(), s.hexdigest().upper()


def candidates(con, cfg, images=False):
    """[(id, path, size, category, full_hash)] of the files to look up, and how many sit in each zone."""
    root = require_root(con)
    lay, segs = disk_layout(con, root)
    max_bytes = cfg["known_max_gb"] * 1024 ** 3
    out = []
    for fid, path, d, name, cat, size, h in con.execute(
            "SELECT id, path, dir, name, category, size, full_hash FROM files WHERE size > 0"):
        if size > max_bytes:
            continue
        zone = lay.file_zone(segs(d), name)[0]
        if is_candidate(cat, name, zone, images):
            out.append((fid, path, size, cat, h))
    return out


def hash_candidates(con, cfg, rows, force=False):
    """Compute SHA-1 for candidates whose content has no 'known' row yet (resumable)."""
    have = set() if force else {h for (h,) in con.execute("SELECT hash FROM known")}
    todo, seen = [], set()
    for fid, path, size, cat, h in rows:
        if h and (h in have or h in seen):
            continue
        if h:
            seen.add(h)
        todo.append((fid, path, size, h))
    total = sum(r[2] for r in todo)
    print("Known-file candidates: %d files; to hash: %d (%s)." % (len(rows), len(todo), human(total)))
    if not todo:
        return 0
    prog = Progress()
    done = n = pending = 0

    def job(r):
        try:
            return r, hash_pair(r[1]), None
        except OSError as err:
            return r, None, str(err)

    with ThreadPoolExecutor(max_workers=cfg["known_workers"]) as ex:
        for start in range(0, len(todo), 1000):
            for (fid, path, size, old), digests, err in ex.map(job, todo[start:start + 1000]):
                n += 1
                done += size
                if err:
                    con.execute("INSERT INTO errors(path,error,at) VALUES(?,?,?)", (path, err, time.time()))
                else:
                    h, sha1 = digests
                    if old is None:
                        con.execute("UPDATE files SET full_hash=? WHERE id=? AND full_hash IS NULL", (h, fid))
                    con.execute("INSERT INTO known(hash, sha1, hashed_at) VALUES(?,?,?) "
                                "ON CONFLICT(hash) DO UPDATE SET sha1=excluded.sha1", (h, sha1, time.time()))
                pending += 1
                if pending >= 500:
                    con.commit()
                    pending = 0
                prog.show("SHA-1: %d/%d · %s/%s · %.1f MB/s" % (
                    n, len(todo), human(done), human(total), done / 1048576 / max(prog.elapsed(), 0.001)))
    con.commit()
    prog.done("SHA-1: %d files in %.0f s." % (len(todo), prog.elapsed()))
    return len(todo)


# ------------------------------------------------------------------ lookups

def _record(con, rows, found, label):
    now = time.time()
    for h, sha1, status, checked in rows:
        marks = ",".join(sorted(set(filter(None, (checked or "").split(","))) | {label}))
        if sha1 in found:
            con.execute("UPDATE known SET status='known', source=?, name=?, checked=?, checked_at=? WHERE hash=?",
                        (label, found[sha1], marks, now, h))
        else:
            con.execute("UPDATE known SET status='unknown', checked=?, checked_at=? WHERE hash=?",
                        (marks, now, h))
    con.commit()


def lookup(con, sets, batch=5000):
    """Check every content not known yet against each set; online sets only see executables never sent."""
    for s in sets:
        if s.offline:
            rows = con.execute("SELECT hash, sha1, status, checked FROM known "
                               "WHERE status IS NULL OR status != 'known'").fetchall()
        else:
            rows = con.execute(
                "SELECT k.hash, k.sha1, k.status, k.checked FROM known k WHERE (k.status IS NULL OR "
                "k.status != 'known') AND (',' || COALESCE(k.checked, '') || ',') NOT LIKE ? AND EXISTS ("
                "SELECT 1 FROM files f WHERE f.full_hash = k.hash AND f.category = 'executable')",
                ("%," + s.label + ",%",)).fetchall()
            if rows:
                print("Sending %d SHA-1 hashes of executables to %s (nothing else leaves this machine)."
                      % (len(rows), s.url))
        hits = 0
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            try:
                found = s.lookup([r[1] for r in part])
            except (OSError, ValueError, urllib.error.URLError) as err:
                print("Warning: %s lookup stopped (%s); the rest is retried on the next run." % (s.label, err),
                      file=sys.stderr)
                break
            hits += len(found)
            _record(con, part, found, s.label)
        print("%s: %d checked, %d known." % (s.label, len(rows), hits))


def open_sets(bloom=(), nsrl=(), online=False):
    sets = []
    try:
        for p in bloom:
            sets.append(BloomSet(p))
        for p in nsrl:
            sets.append(NsrlSet(p))
    except (OSError, ValueError) as err:
        for s in sets:
            s.close()
        sys.exit("Cannot open hash set: %s" % err)
    if online:
        sets.append(OnlineSet())
    return sets


def run_known(con, cfg, bloom=(), nsrl=(), online=False, images=False, force=False, opener=None):
    """'archivero known': hash candidates, look them up, refresh the non-personal flags."""
    require_root(con)
    bloom = list(bloom) or ([cfg["known_bloom"]] if cfg.get("known_bloom") else [])
    nsrl = list(nsrl) or ([cfg["known_nsrl"]] if cfg.get("known_nsrl") else [])
    sets = open_sets(bloom, nsrl, online)
    if opener is not None:
        for s in sets:
            if not s.offline:
                s.opener = opener
    try:
        rows = candidates(con, cfg, images)
        hash_candidates(con, cfg, rows, force)
        if not sets:
            print("No hash set given: SHA-1s are stored, nothing was looked up. Download the CIRCL Bloom filter "
                  "(%s, about 1 GB) and run 'archivero known --bloom hashlookup-full.bloom'." % BLOOM_URL)
        lookup(con, sets)
        if sets:
            prev = json.loads(get_meta(con, "known_sets") or "[]")
            labels = {s.label for s in sets}
            prev = [p for p in prev if p.get("label") not in labels]
            set_meta(con, "known_sets", json.dumps(prev + [dict(s.info, checked_at=time.time()) for s in sets]))
            con.commit()
    finally:
        for s in sets:
            s.close()
    if con.execute("SELECT 1 FROM media LIMIT 1").fetchone():
        from .media_index import flag_nonpersonal
        flag_nonpersonal(con, cfg)
    total, kn, unk = con.execute("SELECT COUNT(*), SUM(status='known'), SUM(status='unknown') FROM known").fetchone()
    print("Known-file filter: %d contents hashed, %d known, %d not in the sets checked, %d not looked up yet."
          % (total or 0, kn or 0, unk or 0, (total or 0) - (kn or 0) - (unk or 0)))
    return {"hashed": total or 0, "known": kn or 0, "unknown": unk or 0}
